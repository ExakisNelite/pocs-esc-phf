
locals {
  workbook_source_id = lower(var.log_analytics_workspace_id)
  # The JSON ships with a __TABLE__ placeholder so the custom table can be renamed without touching the workbook.
  workbook_data_json = replace(
    file("${path.module}/../workbooks/pr-leadtime.workbook.json"),
    "__TABLE__",
    var.log_table_name
  )

  # Shared contract between the custom table and the DCR stream: both must expose the exact same columns.
  pr_metrics_columns = [
    { name = "TimeGenerated", type = "datetime" },
    { name = "c_application", type = "string" },
    { name = "c_repo", type = "string" },
    { name = "c_prNumber", type = "int" },
    { name = "c_prTitle", type = "string" },
    { name = "c_prAuthor", type = "string" },
    { name = "c_prUrl", type = "string" },
    { name = "c_baseRef", type = "string" },
    { name = "c_headRef", type = "string" },
    { name = "c_eventType", type = "string" },
    { name = "c_eventAction", type = "string" },
    { name = "c_actor", type = "string" },
    { name = "c_reviewLabel", type = "string" },
    { name = "c_labels", type = "string" },
    { name = "c_isDraft", type = "boolean" },
    { name = "c_merged", type = "boolean" },
    { name = "c_createdAt", type = "datetime" },
    { name = "c_cycleStartAt", type = "datetime" },
    { name = "c_reviewStartedAt", type = "datetime" },
    { name = "c_closedAt", type = "datetime" },
    { name = "c_leadTimeToReviewSec", type = "int" },
    { name = "c_reviewDurationSec", type = "int" },
    { name = "c_totalLeadTimeSec", type = "int" },
    { name = "c_additions", type = "int" },
    { name = "c_deletions", type = "int" },
    { name = "c_changedFiles", type = "int" },
    { name = "c_commits", type = "int" },
    { name = "c_workflowRunId", type = "string" },
  ]

  # The tables API expects "dateTime" where DCR stream declarations expect "datetime".
  pr_metrics_table_columns = [
    for column in local.pr_metrics_columns : {
      name = column.name
      type = column.type == "datetime" ? "dateTime" : column.type
    }
  ]

  pr_metrics_stream_name = "Custom-${var.log_table_name}"

}