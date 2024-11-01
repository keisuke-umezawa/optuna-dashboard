import {
  FormControl,
  FormLabel,
  Grid,
  MenuItem,
  Select,
  SelectChangeEvent,
  Typography,
  useTheme,
} from "@mui/material"
import * as Optuna from "@optuna/types"
import * as plotly from "plotly.js-dist-min"
import { FC, useEffect, useState } from "react"
import { useGraphComponentState } from "../hooks/useGraphComponentState"
import { makeHovertext } from "../utils/graphUtil"
import { useMergedUnionSearchSpace } from "../utils/searchSpace"
import { GraphContainer } from "./GraphContainer"
import { plotlyDarkTemplate } from "./PlotlyDarkMode"

const plotDomId = "graph-rank"

interface RankPlotInfo {
  xtitle: string
  ytitle: string
  xtype: plotly.AxisType
  ytype: plotly.AxisType
  xvalues: (string | number)[]
  yvalues: (string | number)[]
  colors: number[]
  is_feasible: boolean[]
  hovertext: string[]
}

export const PlotRank: FC<{
  study: Optuna.Study | null
  colorTheme?: Partial<Plotly.Template>
}> = ({ study, colorTheme }) => {
  const theme = useTheme()
  const colorThemeUsed =
    colorTheme ?? (theme.palette.mode === "dark" ? plotlyDarkTemplate : {})
  const { graphComponentState, notifyGraphDidRender } = useGraphComponentState()

  const [objectiveId, setobjectiveId] = useState<number>(0)
  const searchSpace = useMergedUnionSearchSpace(study?.union_search_space)
  const [xParam, setXParam] = useState<Optuna.SearchSpaceItem | null>(null)
  const [yParam, setYParam] = useState<Optuna.SearchSpaceItem | null>(null)
  const metricNames: string[] = study?.metric_names || []

  if (xParam === null && searchSpace.length > 0) {
    setXParam(searchSpace[0])
  }
  if (yParam === null && searchSpace.length > 1) {
    setYParam(searchSpace[1])
  }

  const handleObjectiveChange = (event: SelectChangeEvent<number>) => {
    setobjectiveId(Number(event.target.value))
  }
  const handleXParamChange = (event: SelectChangeEvent<string>) => {
    const param = searchSpace.find((item) => item.name === event.target.value)
    setXParam(param || null)
  }
  const handleYParamChange = (event: SelectChangeEvent<string>) => {
    const param = searchSpace.find((item) => item.name === event.target.value)
    setYParam(param || null)
  }

  useEffect(() => {
    if (study != null && graphComponentState !== "componentWillMount") {
      const rankPlotInfo = getRankPlotInfo(study, objectiveId, xParam, yParam)
      plotRank(rankPlotInfo, colorThemeUsed)?.then(notifyGraphDidRender)
    }
  }, [
    study,
    objectiveId,
    xParam,
    yParam,
    theme.palette.mode,
    colorTheme,
    graphComponentState,
  ])

  const space: Optuna.SearchSpaceItem[] = study ? study.union_search_space : []

  return (
    <Grid container direction="row">
      <Grid
        item
        xs={3}
        container
        direction="column"
        sx={{ paddingRight: theme.spacing(2) }}
      >
        <Typography
          variant="h6"
          sx={{ margin: "1em 0", fontWeight: theme.typography.fontWeightBold }}
        >
          Rank
        </Typography>
        {study !== null && study.directions.length !== 1 ? (
          <FormControl component="fieldset">
            <FormLabel component="legend">Objective:</FormLabel>
            <Select value={objectiveId} onChange={handleObjectiveChange}>
              {study.directions.map((d, i) => (
                <MenuItem value={i} key={i}>
                  {metricNames.length === study?.directions.length
                    ? metricNames[i]
                    : `${i}`}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        ) : null}
        {study !== null && space.length > 0 ? (
          <Grid container direction="column" gap={1}>
            <FormControl component="fieldset" fullWidth>
              <FormLabel component="legend">x:</FormLabel>
              <Select value={xParam?.name || ""} onChange={handleXParamChange}>
                {space.map((d) => (
                  <MenuItem value={d.name} key={d.name}>
                    {d.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl component="fieldset" fullWidth>
              <FormLabel component="legend">y:</FormLabel>
              <Select value={yParam?.name || ""} onChange={handleYParamChange}>
                {space.map((d) => (
                  <MenuItem value={d.name} key={d.name}>
                    {d.name}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>
        ) : null}
      </Grid>
      <Grid item xs={9}>
        <GraphContainer
          plotDomId={plotDomId}
          graphComponentState={graphComponentState}
        />
      </Grid>
    </Grid>
  )
}

const getRankPlotInfo = (
  study: Optuna.Study | null,
  objectiveId: number,
  xParam: Optuna.SearchSpaceItem | null,
  yParam: Optuna.SearchSpaceItem | null
): RankPlotInfo | null => {
  if (study === null) {
    return null
  }

  const trials = study.trials
  const filteredTrials = trials.filter(filterFunc)
  if (filteredTrials.length < 2 || xParam === null || yParam === null) {
    return null
  }

  const xAxis = getAxisInfo(filteredTrials, xParam)
  const yAxis = getAxisInfo(filteredTrials, yParam)

  let xValues: (string | number)[] = []
  let yValues: (string | number)[] = []
  const zValues: number[] = []
  const isFeasible: boolean[] = []
  const hovertext: string[] = []
  filteredTrials.forEach((trial, i) => {
    const xValue = xAxis.values[i]
    const yValue = yAxis.values[i]
    if (xValue && yValue && trial.values) {
      xValues.push(xValue)
      yValues.push(yValue)
      const zValue = trial.values[objectiveId]
      zValues.push(zValue)
      const feasibility = trial.constraints.every((c) => c <= 0)
      isFeasible.push(feasibility)
      hovertext.push(makeHovertext(trial))
    }
  })

  const colors = getColors(zValues)

  if (xAxis.isCat && !yAxis.isCat) {
    const indices: number[] = Array.from(Array(xValues.length).keys()).sort(
      (a, b) =>
        xValues[a]
          .toString()
          .toLowerCase()
          .localeCompare(xValues[b].toString().toLowerCase())
    )
    xValues = indices.map((i) => xValues[i])
    yValues = indices.map((i) => yValues[i])
  } else if (!xAxis.isCat && yAxis.isCat) {
    const indices: number[] = Array.from(Array(yValues.length).keys()).sort(
      (a, b) =>
        yValues[a]
          .toString()
          .toLowerCase()
          .localeCompare(yValues[b].toString().toLowerCase())
    )
    xValues = indices.map((i) => xValues[i])
    yValues = indices.map((i) => yValues[i])
  } else if (xAxis.isCat && yAxis.isCat) {
    const indices: number[] = Array.from(Array(xValues.length).keys()).sort(
      (a, b) => {
        const xComp = xValues[a]
          .toString()
          .toLowerCase()
          .localeCompare(xValues[b].toString().toLowerCase())
        if (xComp !== 0) {
          return xComp
        }
        return yValues[a]
          .toString()
          .toLowerCase()
          .localeCompare(yValues[b].toString().toLowerCase())
      }
    )
    xValues = indices.map((i) => xValues[i])
    yValues = indices.map((i) => yValues[i])
  }

  return {
    xtitle: xAxis.name,
    ytitle: yAxis.name,
    xtype: xAxis.isCat ? "category" : xAxis.isLog ? "log" : "linear",
    ytype: yAxis.isCat ? "category" : yAxis.isLog ? "log" : "linear",
    xvalues: xValues,
    yvalues: yValues,
    colors,
    is_feasible: isFeasible,
    hovertext,
  }
}

const filterFunc = (trial: Optuna.Trial): boolean => {
  return trial.state === "Complete" && trial.values !== undefined
}

const getColors = (values: number[]): number[] => {
  const rawRanks = getOrderWithSameOrderAveraging(values)
  let colorIdxs: number[] = []
  if (values.length > 2) {
    colorIdxs = rawRanks.map((rank) => rank / (values.length - 1))
  } else {
    colorIdxs = [0.5]
  }
  return colorIdxs
}

const getOrderWithSameOrderAveraging = (values: number[]): number[] => {
  const sortedValues = values.slice().sort((a, b) => a - b)
  const ranks: number[] = []
  values.forEach((value) => {
    const firstIndex = sortedValues.indexOf(value)
    const lastIndex = sortedValues.lastIndexOf(value)
    const sumOfTheValue =
      ((firstIndex + lastIndex) * (lastIndex - firstIndex + 1)) / 2
    const rank = sumOfTheValue / (lastIndex - firstIndex + 1)
    ranks.push(rank)
  })
  return ranks
}

const plotRank = (
  rankPlotInfo: RankPlotInfo | null,
  colorTheme: Partial<Plotly.Template>
) => {
  if (document.getElementById(plotDomId) === null) {
    return
  }

  if (rankPlotInfo === null) {
    return plotly.react(plotDomId, [], {
      template: colorTheme,
    })
  }

  const layout: Partial<plotly.Layout> = {
    xaxis: {
      title: rankPlotInfo.xtitle,
      type: rankPlotInfo.xtype,
    },
    yaxis: {
      title: rankPlotInfo.ytitle,
      type: rankPlotInfo.ytype,
    },
    margin: {
      l: 50,
      t: 0,
      r: 50,
      b: 50,
    },
    uirevision: "true",
    template: colorTheme,
  }

  const xValues = rankPlotInfo.xvalues
  const yValues = rankPlotInfo.yvalues

  const plotData: Partial<plotly.PlotData>[] = [
    {
      type: "scatter",
      x: xValues.filter((_, i) => rankPlotInfo.is_feasible[i]),
      y: yValues.filter((_, i) => rankPlotInfo.is_feasible[i]),
      marker: {
        color: rankPlotInfo.colors.filter(
          (_, i) => rankPlotInfo.is_feasible[i]
        ),
        colorscale: "Portland",
        colorbar: {
          title: "Rank",
        },
        size: 10,
        line: {
          color: "Grey",
          width: 0.5,
        },
      },
      mode: "markers",
      showlegend: false,
      hovertemplate: "%{hovertext}<extra></extra>",
      hovertext: rankPlotInfo.hovertext.filter(
        (_, i) => rankPlotInfo.is_feasible[i]
      ),
    },
    {
      type: "scatter",
      x: xValues.filter((_, i) => !rankPlotInfo.is_feasible[i]),
      y: yValues.filter((_, i) => !rankPlotInfo.is_feasible[i]),
      marker: {
        color: "#cccccc",
        size: 10,
        line: {
          color: "Grey",
          width: 0.5,
        },
      },
      mode: "markers",
      showlegend: false,
      hovertemplate: "%{hovertext}<extra></extra>",
      hovertext: rankPlotInfo.hovertext.filter(
        (_, i) => !rankPlotInfo.is_feasible[i]
      ),
    },
  ]
  return plotly.react(plotDomId, plotData, layout)
}
