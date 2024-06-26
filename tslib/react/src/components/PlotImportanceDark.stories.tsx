import { CssBaseline, ThemeProvider } from "@mui/material"
import { Meta, StoryObj } from "@storybook/react"
import React from "react"
import { useMockStudy } from "../MockStudies"
import { darkTheme } from "../styles/darkTheme"
import { PlotImportance } from "./PlotImportance"

const meta: Meta<typeof PlotImportance> = {
  component: PlotImportance,
  title: "PlotImportanceDark",
  tags: ["autodocs"],
  decorators: [
    (Story, storyContext) => {
      const { study, importance } = useMockStudy(
        storyContext.parameters?.studyId
      )
      if (!study) return <p>loading...</p>
      return (
        <ThemeProvider theme={darkTheme}>
          <CssBaseline />
          <Story
            args={{
              study,
              importance,
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
type Story = StoryObj<typeof PlotImportance>

export const MockStudyExample1: Story = {
  parameters: {
    studyId: 1,
  },
}
