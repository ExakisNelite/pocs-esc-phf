terraform {
  required_version = ">= 1.14.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.2"
    }
    azapi = {
      source  = "azure/azapi"
      version = ">= 2.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.0"
    }
  }
}

provider "azurerm" {
  resource_provider_registrations = "all"
  features {
    key_vault {
      purge_soft_delete_on_destroy    = true
      recover_soft_deleted_key_vaults = true
    }
  }
  subscription_id     = var.subscription_id
  storage_use_azuread = true
}