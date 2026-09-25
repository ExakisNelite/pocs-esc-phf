

resource "random_uuid" "pr_leadtime" {
  keepers = {
    source_id = local.workbook_source_id
  }
}

resource "azurerm_application_insights_workbook" "pr_leadtime" {
  name                = var.workbook_name != "" ? var.workbook_name : random_uuid.pr_leadtime.result
  resource_group_name = var.resource_group_name
  location            = var.location
  display_name        = var.workbook_display_name
  source_id           = local.workbook_source_id
  category            = "workbook"
  data_json           = local.workbook_data_json
  tags                = var.tags
}

resource "azurerm_monitor_data_collection_endpoint" "pr_metrics" {
  name                          = var.data_collection_endpoint_name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  public_network_access_enabled = var.public_network_access_enabled
  description                   = "Logs ingestion endpoint for GitHub pull request metrics."
  tags                          = var.tags
}

resource "azurerm_monitor_data_collection_rule" "pr_metrics" {
  name                        = var.data_collection_rule_name
  resource_group_name         = var.resource_group_name
  location                    = var.location
  data_collection_endpoint_id = azurerm_monitor_data_collection_endpoint.pr_metrics.id
  description                 = "Routes GitHub pull request lifecycle events to the custom table."
  tags                        = var.tags

  destinations {
    log_analytics {
      name                  = "law-destination"
      workspace_resource_id = var.log_analytics_workspace_id
    }
  }

  stream_declaration {
    stream_name = local.pr_metrics_stream_name

    dynamic "column" {
      for_each = local.pr_metrics_columns
      content {
        name = column.value.name
        type = column.value.type
      }
    }
  }

  data_flow {
    streams       = [local.pr_metrics_stream_name]
    destinations  = ["law-destination"]
    output_stream = local.pr_metrics_stream_name
    transform_kql = "source"
  }

  depends_on = [azapi_resource.pr_metrics_table]
}

resource "azurerm_role_assignment" "pr_metrics_publisher" {
  for_each = toset(var.ingestion_principal_ids)

  scope                = azurerm_monitor_data_collection_rule.pr_metrics.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = each.value
}

resource "azapi_resource" "pr_metrics_table" {
  type      = "Microsoft.OperationalInsights/workspaces/tables@2022-10-01"
  name      = var.log_table_name
  parent_id = var.log_analytics_workspace_id

  body = {
    properties = {
      retentionInDays      = var.table_retention_in_days
      totalRetentionInDays = var.table_total_retention_in_days
      schema = {
        name        = var.log_table_name
        description = "Pull request lifecycle events used to track review lead time."
        columns     = local.pr_metrics_table_columns
      }
    }
  }
}
