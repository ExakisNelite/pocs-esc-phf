output "pr_leadtime_workbook_id" {
  description = "Resource ID of the pull request lead time workbook."
  value       = azurerm_application_insights_workbook.pr_leadtime.id
}

output "pr_leadtime_workbook_name" {
  description = "GUID used as the workbook resource name."
  value       = azurerm_application_insights_workbook.pr_leadtime.name
}

output "pr_metrics_table_name" {
  description = "Custom table receiving the pull request lifecycle events."
  value       = azapi_resource.pr_metrics_table.name
}

output "pr_metrics_dcr_id" {
  description = "Resource ID of the data collection rule."
  value       = azurerm_monitor_data_collection_rule.pr_metrics.id
}

output "pr_metrics_dcr_immutable_id" {
  description = "Immutable ID of the data collection rule."
  value       = azurerm_monitor_data_collection_rule.pr_metrics.immutable_id
}

output "pr_metrics_logs_ingestion_endpoint" {
  description = "Logs ingestion endpoint of the data collection endpoint."
  value       = azurerm_monitor_data_collection_endpoint.pr_metrics.logs_ingestion_endpoint
}

# Value to set as the GitHub repository variable LA_DCR_PR_INGEST_URL.
output "pr_metrics_ingest_url" {
  description = "Full logs ingestion URL consumed by the pr-monitoring GitHub workflow."
  value       = "${azurerm_monitor_data_collection_endpoint.pr_metrics.logs_ingestion_endpoint}/dataCollectionRules/${azurerm_monitor_data_collection_rule.pr_metrics.immutable_id}/streams/${local.pr_metrics_stream_name}?api-version=2023-01-01"
}
