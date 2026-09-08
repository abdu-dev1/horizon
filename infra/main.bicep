// Horizon on Azure App Service (Linux container).
//
// Deploy:  see infra/deploy.ps1 -- it builds/pushes the image first, because
// the Web App will not start healthy without one.
//
// Shape and why:
//   * ONE Web App running one container that supervises three processes. Both
//     backends ship a package named `app` and cannot share an interpreter, so
//     they stay separate processes (gateway/main.py explains this); the
//     container's entrypoint is gateway/run_app.py.
//   * SINGLE instance, deliberately. The app persists state by writing CSVs,
//     which is not concurrency-safe -- two instances would interleave writes to
//     the same file. alwaysOn keeps it warm instead of scaling out. The cost is
//     that a restart is a short outage; for an internal tool that is the right
//     trade, but it is a real property to know about.
//   * Azure Files mounted for data/models. The image is code only and is
//     replaced on every deploy, so anything that must outlive a deploy lives on
//     the share. Raw client workbooks are never in the image OR the share --
//     they stay on the admin's laptop where retraining happens.
//   * Easy Auth (Entra ID) in front, with the health probe path excluded.

targetScope = 'resourceGroup'

@description('Short name used as a prefix for every resource. Lowercase letters and digits only.')
@minLength(3)
@maxLength(11)
param namePrefix string = 'horizon'

@description('Azure region. Defaults to the resource group\'s location.')
param location string = resourceGroup().location

@description('Container image tag to deploy, e.g. "2026-09-08-1". Change this to roll forward.')
param imageTag string = 'latest'

@description('Entra ID application (client) ID for the app registration that fronts this site.')
param entraClientId string

@description('Entra tenant ID.')
param entraTenantId string = subscription().tenantId

@description('Client secret for the Entra app registration. Stored as a slot-sticky app setting.')
@secure()
param entraClientSecret string

@description('Comma-separated emails granted admin (model publishing, data-quality internals). An empty value grants NO ONE admin -- see gateway/auth.py.')
param adminEmails string = ''

@description('App Service plan SKU. The New Business model alone is ~148 MB resident, so B1/free tiers will thrash or OOM.')
@allowed(['B2', 'B3', 'P0v3', 'P1v3', 'P2v3'])
param planSku string = 'B2'

var acrName = '${namePrefix}acr${uniqueString(resourceGroup().id)}'
var storageName = '${namePrefix}st${uniqueString(resourceGroup().id)}'
var planName = '${namePrefix}-plan'
var siteName = '${namePrefix}-app'
var imageName = 'horizon'

// File shares: one per product, matching the two data roots the container
// expects (HORIZON_RENEWAL_DATA_DIR / HORIZON_NB_DATA_DIR). Kept separate
// rather than one share with subfolders so the two projects' isolation rule
// holds at the storage layer too.
var shareRenewal = 'renewal-data'
var shareNb = 'nb-data'

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: {
    // The Web App authenticates to ACR with its managed identity (AcrPull
    // below), so the admin user stays off.
    adminUserEnabled: false
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource renewalShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: shareRenewal
  properties: { shareQuota: 16 }
}

resource nbShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: shareNb
  // Larger: this one holds the ~148 MB model plus history/pipeline CSVs, and
  // published bundles land here transiently.
  properties: { shareQuota: 32 }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  sku: { name: planSku }
  kind: 'linux'
  properties: { reserved: true }  // reserved: true == Linux
}

resource site 'Microsoft.Web/sites@2023-12-01' = {
  name: siteName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'DOCKER|${acr.properties.loginServer}/${imageName}:${imageTag}'
      acrUseManagedIdentityCreds: true
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      // Health probe. gateway/main.py::healthz checks BOTH engines, so a dead
      // engine surfaces as an unhealthy instance rather than a dashboard that
      // 502s half its pages. It is in auth.PUBLIC_PATHS because an
      // authenticated probe path would fail and restart the container forever.
      healthCheckPath: '/healthz'
      appSettings: [
        // Which port App Service forwards to inside the container.
        { name: 'WEBSITES_PORT', value: '8000' }
        // Give the container time to load two models and build initial state
        // before the platform decides it failed to start.
        { name: 'WEBSITES_CONTAINER_START_TIME_LIMIT', value: '600' }
        { name: 'HORIZON_ADMIN_EMAILS', value: adminEmails }
        // Belt and braces: the image already pins easyauth (see Dockerfile),
        // this makes it visible in the portal rather than implicit.
        { name: 'HORIZON_AUTH_MODE', value: 'easyauth' }
        { name: 'MICROSOFT_PROVIDER_AUTHENTICATION_SECRET', value: entraClientSecret }
      ]
    }
  }
  // No dependsOn: acrPull -- that would be circular, since the role assignment
  // needs this site's managed-identity principalId. Consequence: on a FIRST
  // deploy the site may report a pull failure for a minute until the AcrPull
  // assignment lands, then succeed on its own retry. That is expected, not a
  // broken template.
}

// Mount the shares at the paths the container's env vars already point to.
resource mounts 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: site
  name: 'azurestorageaccounts'
  properties: {
    renewaldata: {
      type: 'AzureFiles'
      accountName: storage.name
      shareName: shareRenewal
      mountPath: '/var/horizon/renewal'
      accessKey: storage.listKeys().keys[0].value
    }
    nbdata: {
      type: 'AzureFiles'
      accountName: storage.name
      shareName: shareNb
      mountPath: '/var/horizon/nb'
      accessKey: storage.listKeys().keys[0].value
    }
  }
  dependsOn: [ renewalShare, nbShare ]
}

// Entra ID sign-in, enforced by the platform BEFORE any request reaches the
// container. This is why gateway/auth.py trusts the principal header and never
// validates a token: Azure strips any client-supplied copy and injects its own.
resource auth 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: site
  name: 'authsettingsV2'
  properties: {
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
      // The probe must stay reachable anonymously.
      excludedPaths: [ '/healthz' ]
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          openIdIssuer: '${environment().authentication.loginEndpoint}${entraTenantId}/v2.0'
          clientId: entraClientId
          clientSecretSettingName: 'MICROSOFT_PROVIDER_AUTHENTICATION_SECRET'
        }
        validation: {
          allowedAudiences: [ 'api://${entraClientId}' ]
        }
      }
    }
    login: {
      tokenStore: { enabled: true }
    }
  }
  dependsOn: [ mounts ]
}

// Let the site pull from ACR with its managed identity instead of a stored
// registry password.
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  // AcrPull
  name: guid(acr.id, siteName, '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  properties: {
    principalId: site.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}

output siteUrl string = 'https://${site.properties.defaultHostName}'
output acrLoginServer string = acr.properties.loginServer
output acrNameOut string = acr.name
output storageAccount string = storage.name
output renewalShareName string = shareRenewal
output nbShareName string = shareNb
