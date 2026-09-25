variable "resource_group_name" {
  description = "Resource group hosting the workbook."
  type        = string
}

variable "location" {
  description = "Azure region of the workbook."
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Resource ID of the Log Analytics workspace queried by the workbook."
  type        = string

  validation {
    condition     = can(regex("(?i)/providers/Microsoft.OperationalInsights/workspaces/", var.log_analytics_workspace_id))
    error_message = "log_analytics_workspace_id must be a Log Analytics workspace resource ID."
  }
}

variable "log_table_name" {
  description = "Custom table storing the pull request lifecycle events."
  type        = string
  default     = "PullRequestMetrics_CL"

  validation {
    condition     = endswith(var.log_table_name, "_CL")
    error_message = "A DCR-based custom table name must end with _CL."
  }
}

variable "table_retention_in_days" {
  description = "Interactive retention of the custom table, in days."
  type        = number
  default     = 90
}

variable "table_total_retention_in_days" {
  description = "Total retention (interactive + long term) of the custom table, in days."
  type        = number
  default     = 730
}

variable "data_collection_endpoint_name" {
  description = "Name of the data collection endpoint exposing the logs ingestion URL."
  type        = string
  default     = "dce-pr-metrics"
}

variable "data_collection_rule_name" {
  description = "Name of the data collection rule routing events to the custom table."
  type        = string
  default     = "dcr-pr-metrics"
}

variable "public_network_access_enabled" {
  description = "Whether the data collection endpoint accepts traffic from public networks. GitHub-hosted runners require true."
  type        = bool
  default     = true
}

variable "ingestion_principal_ids" {
  description = "Object IDs of the identities allowed to push events (the GitHub OIDC service principals)."
  type        = list(string)
  default     = []
}

variable "workbook_display_name" {
  description = "Display name of the workbook in the Azure portal."
  type        = string
  default     = "Pull Request - Lead time de revue"
}

variable "workbook_name" {
  description = "GUID used as the workbook resource name. Generated when left empty."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags applied to the workbook."
  type        = map(string)
  default     = {}
}
 