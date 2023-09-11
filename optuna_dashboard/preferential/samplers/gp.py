from __future__ import annotations

import math
from typing import Any
from typing import Callable

import botorch.acquisition.analytic
import botorch.models.model
import botorch.optim
import botorch.posteriors.gpytorch
import gpytorch.constraints
import gpytorch.kernels
from gpytorch.likelihoods.gaussian_likelihood import Prior
import numpy as np
import optuna
import optuna._transform
import torch
from torch import Tensor

from .._system_attrs import get_preferences


def _orthants_MVN_Gibbs_sampling(cov_inv: Tensor, cycles: int, initial_sample: Tensor) -> Tensor:
    dim = cov_inv.shape[0]
    assert cov_inv.shape == (dim, dim)

    sample_chain = initial_sample
    conditional_std = torch.rsqrt(torch.diag(cov_inv))
    scaled_cov_inv = cov_inv / torch.diag(cov_inv)[:, None]

    out = torch.empty((cycles + 1, dim), dtype=torch.float64)
    out[0, :] = sample_chain

    for i in range(cycles):
        for j in range(dim):
            conditional_mean = sample_chain[j] - scaled_cov_inv[j] @ sample_chain
            sample_chain[j] = (
                _one_side_trunc_norm_sampling(lower=-conditional_mean / conditional_std[j])
                * conditional_std[j]
                + conditional_mean
            )
        out[i + 1, :] = sample_chain

    return out


def _one_side_trunc_norm_sampling(lower: Tensor) -> Tensor:
    if lower > 4.0:
        r = torch.clamp_min(torch.rand(torch.Size(()), dtype=torch.float64), min=1e-300)
        return (lower * lower - 2 * r.log()).sqrt()
    else:
        SQRT2 = math.sqrt(2)
        r = torch.rand(torch.Size(()), dtype=torch.float64) * torch.erfc(lower / SQRT2)
        while 1 - r == 1:
            r = torch.rand(torch.Size(()), dtype=torch.float64) * torch.erfc(lower / SQRT2)
        return torch.erfinv(1 - r) * SQRT2


_orthants_MVN_Gibbs_sampling_jit = torch.jit.script(_orthants_MVN_Gibbs_sampling)


def _compute_cov_diff_diff_inv(preferences: Tensor, cov_x_x: Tensor, noise_var: Tensor) -> Tensor:
    N = cov_x_x.shape[0]
    M = preferences.shape[0]

    # (sI + A K A^T)^-1 = s^-1 I - s^-2 A(K^-1 + s^-1 A^T A)^-1 A^T
    # (K^-1 + s^-1 A^T A)^-1 = K (I + s^-1 A^T A K)^-1  (To avoid computing K^-1)

    I_plus_sinv_AT_A_K = torch.eye(N, dtype=torch.float64)
    A_K = cov_x_x[preferences[:, 0], :] - cov_x_x[preferences[:, 1], :]
    I_plus_sinv_AT_A_K.index_add_(0, preferences[:, 0], A_K * (1 / noise_var))
    I_plus_sinv_AT_A_K.index_add_(0, preferences[:, 1], A_K * (-1 / noise_var))
    schur_inv: Tensor = torch.linalg.solve(I_plus_sinv_AT_A_K, cov_x_x, left=False)
    cov_diff_diff_inv = schur_inv[:, preferences[:, 0]] - schur_inv[:, preferences[:, 1]]
    cov_diff_diff_inv = (
        cov_diff_diff_inv[preferences[:, 0], :] - cov_diff_diff_inv[preferences[:, 1], :]
    )
    cov_diff_diff_inv *= -1 / noise_var**2
    idx_M = torch.arange(M)
    cov_diff_diff_inv[idx_M, idx_M] += 1.0 / noise_var

    return cov_diff_diff_inv


class _SampledGP(botorch.models.model.Model):
    def __init__(
        self,
        kernel_func: Callable[[Tensor, Tensor], Tensor],
        x: Tensor,
        preferences: Tensor,
        noise_var: Tensor,
        diff: Tensor,
    ) -> None:
        super().__init__()
        self.kernel_func = kernel_func
        self.x = x
        self.preferences = preferences
        self.diff = diff
        self.noise_var = noise_var
        self._cov_diff_diff_inv = _compute_cov_diff_diff_inv(
            preferences=preferences,
            cov_x_x=self.kernel_func(x, x),
            noise_var=noise_var,
        )

    def posterior(
        self,
        X: Tensor,
        output_indices: list[int] | None = None,
        observation_noise: bool = False,
        posterior_transform: Any | None = None,
        **kwargs: Any,
    ) -> botorch.posteriors.gpytorch.GPyTorchPosterior:
        assert posterior_transform is None
        assert output_indices is None
        assert self.x.shape[-1] == X.shape[-1]

        x_expanded = self.x.expand(X.shape[:-2] + (self.x.shape[-2], X.shape[-1]))

        cov_X_x = self.kernel_func(X, x_expanded)
        cov_X_diff = cov_X_x[..., self.preferences[:, 0]] - cov_X_x[..., self.preferences[:, 1]]

        mean = cov_X_diff @ (self._cov_diff_diff_inv @ self.diff)
        cov = self.kernel_func(X, X) - cov_X_diff @ self._cov_diff_diff_inv @ cov_X_diff.transpose(
            -1, -2
        )
        if observation_noise:
            idx = torch.arange(cov.shape[-1])
            cov[..., idx, idx] += self.noise_var

        return botorch.posteriors.gpytorch.GPyTorchPosterior(
            distribution=gpytorch.distributions.MultivariateNormal(
                mean=mean,
                covariance_matrix=cov,
            )
        )

    @property
    def batch_shape(self) -> torch.Size:
        return torch.Size()

    @property
    def num_outputs(self) -> int:
        return 1


def _truncnorm_mean_var_logz(alpha: Tensor) -> tuple[Tensor, Tensor, Tensor]:
    SQRT_HALF = math.sqrt(0.5)
    SQRT_HALF_PI = math.sqrt(0.5 * math.pi)
    logz = torch.special.log_ndtr(-alpha)
    mean = 1 / (SQRT_HALF_PI * torch.special.erfcx(alpha * SQRT_HALF))
    var = 1 - mean * (mean - alpha)
    return mean, var, logz


def _orthants_MVN_EP(
    cov0: Tensor, preferences: Tensor, noise_var: Tensor, cycles: int
) -> tuple[Tensor, Tensor, Tensor]:
    N = cov0.shape[0]
    M = preferences.shape[0]
    mu = torch.zeros(N, dtype=cov0.dtype)
    cov = cov0.clone()
    virtual_obs_a = [torch.tensor(0.0, dtype=cov0.dtype) for _ in range(M)]
    virtual_obs_b = [torch.tensor(0.0, dtype=cov0.dtype) for _ in range(M)]
    log_zs = torch.zeros(M, dtype=cov0.dtype)

    for _ in range(cycles):
        for i in range(M):
            pref_i = preferences[i, :]
            mean1 = mu[pref_i[0]] - mu[pref_i[1]]
            Sxy = cov[pref_i[0]] - cov[pref_i[1]]
            var1 = Sxy[pref_i[0]] - Sxy[pref_i[1]]

            r0 = (1 - var1 * virtual_obs_a[i]).reciprocal()
            var0 = var1 * r0
            mean0 = (mean1 + var1 * virtual_obs_b[i]) * r0

            obs_var = var0 + noise_var
            obs_sigma = torch.sqrt(obs_var)
            alpha = -mean0 / torch.clamp_min(obs_sigma, min=1e-20)
            mean_norm, var_norm, logz = _truncnorm_mean_var_logz(alpha)

            kalman_factor = var0 / torch.clamp_min(obs_var, min=1e-20)
            mean2 = mean0 + obs_sigma * mean_norm * kalman_factor
            var2 = kalman_factor * (noise_var + var_norm * var0)

            var1_var2_inv = torch.clamp_min(var1 * var2, min=1e-20).reciprocal()
            db = (mean1 * var2 - mean2 * var1) * var1_var2_inv
            da = (var1 - var2) * var1_var2_inv
            virtual_obs_b[i] = virtual_obs_b[i] + db
            virtual_obs_a[i] = virtual_obs_a[i] + da

            dr = (1 + var1 * da).reciprocal()
            mu = mu - Sxy * ((db + mean1 * da) * dr)
            cov = cov - (Sxy[:, None] * (da * dr)) @ Sxy[None, :]
            log_zs[i] = logz
    return mu, cov, torch.sum(log_zs)


_orthants_MVN_EP_jit = torch.jit.script(_orthants_MVN_EP)


class _PreferentialGP:
    def __init__(self, kernel: gpytorch.kernels.Kernel, noise_prior: Prior, dims: int) -> None:
        self.kernel = kernel
        self.noise_prior = noise_prior
        self.dims = dims

        self.diff = torch.empty((0,), dtype=torch.float64, requires_grad=False)
        self.log_noise = torch.nn.Parameter(
            torch.tensor(0.0, dtype=torch.float64), requires_grad=True
        )

    def fit_params_EP(self, X: Tensor, preferences: Tensor) -> None:
        if len(preferences) == 0:
            return
        tolerance = 1e-3
        max_iter = 100

        optim = torch.optim.LBFGS([*self.kernel.parameters(), self.log_noise])

        last_params = [p.detach().clone() for p in optim.param_groups[0]["params"]]
        for _ in range(max_iter):

            def closure() -> Tensor:
                optim.zero_grad()
                noise = self.log_noise.exp()
                cov0 = self.kernel.forward(X, X).to_dense()
                _, _, logz = _orthants_MVN_EP_jit(cov0, preferences, noise, cycles=2)

                loss = -logz - self.noise_prior.log_prob(noise)
                for _, _, prior, param, _ in self.kernel.named_priors():
                    loss = loss - prior.log_prob(param(self.kernel)).sum()

                loss.backward()
                return loss

            optim.step(closure)

            # Check for convergence
            params = optim.param_groups[0]["params"]
            for p_old, p_new in zip(last_params, params):
                if torch.max(torch.abs(p_old - p_new)) > tolerance:
                    break
            else:
                break
            last_params = [p.detach().clone() for p in params]

    def sample_gp(self, x: Tensor, preferences: Tensor) -> _SampledGP:
        self.fit_params_EP(x, preferences)

        with torch.no_grad():
            cov_diff_diff_inv = _compute_cov_diff_diff_inv(
                preferences=preferences,
                cov_x_x=self.kernel(x, x).to_dense(),
                noise_var=self.log_noise.exp(),
            )

            original_diff_size = len(self.diff)
            self.diff.resize_(len(preferences))
            self.diff[original_diff_size:] = 0.0

            self.diff = _orthants_MVN_Gibbs_sampling_jit(
                cov_inv=cov_diff_diff_inv,
                initial_sample=self.diff,
                cycles=20,
            )[-1]
            return _SampledGP(
                kernel_func=lambda x1, x2: self.kernel(x1, x2).to_dense(),
                x=x,
                preferences=preferences,
                noise_var=self.log_noise.exp(),
                diff=self.diff,
            )


class PreferentialGPSampler(optuna.samplers.BaseSampler):
    def __init__(
        self,
        *,
        kernel: gpytorch.kernels.Kernel | None = None,
        noise_prior: Prior | None = None,
        independent_sampler: optuna.samplers.BaseSampler | None = None,
        seed: int | None = None,
    ) -> None:
        self.kernel = kernel
        self.noise_prior = noise_prior or gpytorch.priors.GammaPrior(5.0, 50.0)

        self._rng = np.random.RandomState(seed)
        self.independent_sampler = independent_sampler or optuna.samplers.RandomSampler(
            seed=self._rng.randint(2**32)
        )

        self._search_space = optuna.search_space.IntersectionSearchSpace()
        self._gp: _PreferentialGP | None = None

    def reseed_rng(self) -> None:
        self.independent_sampler.reseed_rng()
        self._rng = np.random.RandomState()

    def infer_relative_search_space(
        self, study: optuna.Study, trial: optuna.trial.FrozenTrial
    ) -> dict[str, optuna.distributions.BaseDistribution]:
        return self._search_space.calculate(study)

    def sample_relative(
        self,
        study: optuna.Study,
        trial: optuna.trial.FrozenTrial,
        search_space: dict[str, optuna.distributions.BaseDistribution],
    ) -> dict[str, Any]:
        preferences = get_preferences(study.system_attrs)
        if len(preferences) == 0:
            return {}

        trials = study.get_trials(deepcopy=False)
        trials_with_preference = list({t for (b, w) in preferences for t in (b, w)})
        ids = {t: i for i, t in enumerate(trials_with_preference)}

        trans = optuna._transform._SearchSpaceTransform(
            search_space, transform_log=True, transform_step=True, transform_0_1=True
        )
        params = torch.tensor(
            np.array([trans.transform(trials[t].params) for t in trials_with_preference]),
            dtype=torch.float64,
        )
        pref_ids = torch.tensor([[ids[b], ids[w]] for b, w in preferences], dtype=torch.int32)
        with torch.random.fork_rng():
            torch.manual_seed(self._rng.randint(2**32))

            self._gp = self._gp or _PreferentialGP(
                kernel=self.kernel
                or gpytorch.kernels.MaternKernel(
                    nu=1.5,
                    ard_num_dims=len(trans.bounds),
                    lengthscale_prior=gpytorch.priors.GammaPrior(5.0, 10.0),
                    lengthscale_constraint=gpytorch.constraints.GreaterThan(
                        0.0,
                        transform=torch.exp,
                        inv_transform=torch.log,
                    ),
                ),
                noise_prior=self.noise_prior,
                dims=len(trans.bounds),
            )
            if self._gp.dims != len(trans.bounds):
                raise NotImplementedError(
                    "The search space has changed. "
                    "Dynamic search space is not supported in PreferentialGPSampler."
                )

            sampled_gp = self._gp.sample_gp(params, pref_ids)
            acqf = botorch.acquisition.analytic.LogExpectedImprovement(
                model=sampled_gp,
                best_f=torch.max(sampled_gp.posterior(params[:, None, :]).mean),
            )

            # TODO: Make it possible to apply it on categorical variables
            candidates, _ = botorch.optim.optimize_acqf(
                acq_function=acqf,
                bounds=torch.from_numpy(trans.bounds.T),
                q=1,
                num_restarts=10,
                raw_samples=512,
                options={"batch_limit": 5, "maxiter": 200},
                sequential=True,
            )
            next_x = trans.untransform(candidates[0].detach().numpy())
            return next_x

    def sample_independent(
        self,
        study: optuna.Study,
        trial: optuna.trial.FrozenTrial,
        param_name: str,
        param_distribution: optuna.distributions.BaseDistribution,
    ) -> Any:
        return self.independent_sampler.sample_independent(
            study, trial, param_name, param_distribution
        )