import { CssBaseline, ThemeProvider } from "@mui/material"
import { Meta, StoryObj } from "@storybook/react"
import React from "react"
import { useMockStudy } from "../MockStudies"
import { darkTheme } from "../styles/darkTheme"
import { PlotHistory } from "./PlotHistory"

const meta: Meta<typeof PlotHistory> = {
  component: PlotHistory,
  title: "PlotHistoryDark",
  tags: ["autodocs"],
  decorators: [
    (Story, storyContext) => {
      const { study } = useMockStudy(storyContext.parameters?.studyId)
      if (!study) return <p>loading...</p>
      return (
        <ThemeProvider theme={darkTheme}>
          <CssBaseline />
          <Story
            args={{
              study,
            }}
          />
        </ThemeProvider>
      )
    },
  ],
  parameters: {
    backgrounds: { default: "dark" },
  },
}

export default meta
type Story = StoryObj<typeof PlotHistory>

export const MockStudyExample1: Story = {
  parameters: {
    studyId: 1,
  },
}
