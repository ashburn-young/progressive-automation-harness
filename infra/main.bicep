// main.bicep - Progressive Automation Harness environment on Azure.
//
// Provisions the AI Agent Harness described in the architecture, REUSING the
// resources that already exist in the resource group (AI Services "Brain",
// Application Insights, Key Vault) and adding the orchestration, tools, memory,
// governance, and telemetry layers.
//
// Expensive resources are behind feature flags (default off) so you can deploy a
// low-cost foundation first and turn on APIM / AI Search / Redis when needed.
//
// Deploy (resource-group scope):
//   az deployment group create -g rg-takemyjob -f infra/main.bicep -p infra/main.bicepparam

targetScope = 'resourceGroup'

@description('Location for new resources.')
param location string = resourceGroup().location

@description('Short prefix for resource names (lowercase).')
@minLength(2)
@maxLength(12)
param namePrefix string = 'pah'

// -- Reuse existing resources -------------------------------------------------
@description('Existing AI Services (Foundry) account name - the model "Brain".')
param existingAiServicesName string = 'takemyjob'

@description('Existing Application Insights component name.')
param existingAppInsightsName string = 'airgtakemyjob03b5ad'

@description('Existing Key Vault name.')
param existingKeyVaultName string = 'kvrgtakemyjob03b5ad'

// -- Feature flags (cost control) --------------------------------------------
@description('Deploy Azure AI Search (Basic ~ paid). Long-term memory / RAG.')
param deployAiSearch bool = true

@description('Deploy Azure Cache for Redis (Basic C0 ~ paid). Short-term state.')
param deployRedis bool = false

@description('Deploy API Management (Developer SKU ~ paid, slow to provision).')
param deployApim bool = false

@description('Deploy an Azure Functions app for skill execution.')
param deployFunctions bool = true

@description('Publisher email for API Management (required if deployApim).')
param apimPublisherEmail string = 'admin@mngenvmcap999938.onmicrosoft.com'

@description('Publisher name for API Management.')
param apimPublisherName string = 'TakeMyJob'

@description('Disable public network access on Cosmos/Search once the orchestrator runs inside the VNet. Leave false while the harness runs outside Azure.')
param lockDownPublicAccess bool = false

var publicAccess = lockDownPublicAccess ? 'Disabled' : 'Enabled'

var suffix = uniqueString(resourceGroup().id)
var laName = '${namePrefix}-law-${suffix}'
var uamiName = '${namePrefix}-id-${suffix}'
var acaEnvName = '${namePrefix}-acaenv-${suffix}'
var orchestratorName = '${namePrefix}-orchestrator'
var storageName = toLower('${namePrefix}st${suffix}')
var funcPlanName = '${namePrefix}-funcplan-${suffix}'
var funcAppName = '${namePrefix}-func-${suffix}'
var cosmosName = toLower('${namePrefix}-cosmos-${suffix}')
var searchName = toLower('${namePrefix}-search-${suffix}')
var redisName = toLower('${namePrefix}-redis-${suffix}')
var apimName = '${namePrefix}-apim-${suffix}'
var acrName = toLower('${namePrefix}acr${suffix}')
var vnetName = '${namePrefix}-vnet-${suffix}'
var peZones = [
  'privatelink.cognitiveservices.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.services.ai.azure.com'
  'privatelink.documents.azure.com'
  'privatelink.search.windows.net'
]

// ---------------------------------------------------------------------------
// Existing resources
// ---------------------------------------------------------------------------
resource aiServices 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: existingAiServicesName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: existingAppInsightsName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: existingKeyVaultName
}

// ---------------------------------------------------------------------------
// Identity - a single user-assigned identity the harness runs as
// ---------------------------------------------------------------------------
resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: uamiName
  location: location
}

// ---------------------------------------------------------------------------
// Observability - Log Analytics (App Insights already exists)
// ---------------------------------------------------------------------------
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: laName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ---------------------------------------------------------------------------
// Orchestration - Container Apps environment + the orchestrator app
// ---------------------------------------------------------------------------
resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: acaEnvName
  location: location
  properties: {
    workloadProfiles: [
      { name: 'Consumption', workloadProfileType: 'Consumption' }
    ]
    vnetConfiguration: {
      infrastructureSubnetId: '${vnet.id}/subnets/snet-aca'
      internal: false
    }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
  }
}

resource orchestrator 'Microsoft.App/containerApps@2024-03-01' = {
  name: orchestratorName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${uami.id}': {} }
  }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        { server: acr.properties.loginServer, identity: uami.id }
      ]
    }
    template: {
      containers: [
        {
          // Placeholder image; replace with the harness container image.
          name: 'orchestrator'
          image: 'mcr.microsoft.com/k8se/quickstart:latest'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AZURE_AI_ENDPOINT', value: 'https://takemyjob.services.ai.azure.com/api/projects/proj-default/openai/v1' }
            { name: 'AZURE_AI_DEPLOYMENT', value: 'gpt-5.6-sol' }
            { name: 'AZURE_CLIENT_ID', value: uami.properties.clientId }
            { name: 'COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
            { name: 'SEARCH_ENDPOINT', value: 'https://${searchName}.search.windows.net' }
            { name: 'AZURE_AI_EMBED_DEPLOYMENT', value: 'text-embedding-3-small' }
            { name: 'AZURE_AI_MODELS', value: 'gpt-5.6-sol,gpt-5.6-luna,gpt-5.4-mini,gpt-5.4-nano' }
            { name: 'FABRIC_WORKSPACE_ID', value: '129cfc63-73d6-4dac-82af-a73eaaec211b' }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 3 }
    }
  }
}

// ---------------------------------------------------------------------------
// Tools execution - Storage + Functions (serverless skills)
// ---------------------------------------------------------------------------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = if (deployFunctions) {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource funcPlan 'Microsoft.Web/serverfarms@2023-12-01' = if (deployFunctions) {
  name: funcPlanName
  location: location
  sku: { name: 'Y1', tier: 'Dynamic' }
  properties: {}
}

resource funcApp 'Microsoft.Web/sites@2023-12-01' = if (deployFunctions) {
  name: funcAppName
  location: location
  kind: 'functionapp'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${uami.id}': {} }
  }
  properties: {
    serverFarmId: funcPlan.id
    httpsOnly: true
    siteConfig: {
      appSettings: [
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'AzureWebJobsStorage__accountName', value: storageName }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
      ]
    }
  }
}

// ---------------------------------------------------------------------------
// Memory - Cosmos DB (short-term state) + AI Search (long-term / RAG)
// ---------------------------------------------------------------------------
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [ { locationName: location, failoverPriority: 0 } ]
    capabilities: [ { name: 'EnableServerless' } ]
    disableLocalAuth: true
    publicNetworkAccess: publicAccess
  }
}

resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmos
  name: 'harness'
  properties: { resource: { id: 'harness' } }
}

resource cosmosState 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDb
  name: 'sessions'
  properties: {
    resource: {
      id: 'sessions'
      partitionKey: { paths: [ '/sessionId' ], kind: 'Hash' }
    }
  }
}

resource cosmosSkills 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDb
  name: 'skills'
  properties: {
    resource: {
      id: 'skills'
      partitionKey: { paths: [ '/slug' ], kind: 'Hash' }
    }
  }
}

resource cosmosLogs 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDb
  name: 'trainingLogs'
  properties: {
    resource: {
      id: 'trainingLogs'
      partitionKey: { paths: [ '/taskName' ], kind: 'Hash' }
    }
  }
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = if (deployAiSearch) {
  name: searchName
  location: location
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: publicAccess
    disableLocalAuth: true
  }
}

resource redis 'Microsoft.Cache/redis@2024-03-01' = if (deployRedis) {
  name: redisName
  location: location
  properties: {
    sku: { name: 'Basic', family: 'C', capacity: 0 }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
  }
}

// ---------------------------------------------------------------------------
// Container registry - holds the orchestrator image (built via ACR Tasks)
// ---------------------------------------------------------------------------
resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
  }
}

// ---------------------------------------------------------------------------
// Governance - API Management (optional; gateway / throttling / cost control)
// ---------------------------------------------------------------------------
resource apim 'Microsoft.ApiManagement/service@2023-05-01-preview' = if (deployApim) {
  name: apimName
  location: location
  sku: { name: 'Developer', capacity: 1 }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: apimPublisherEmail
    publisherName: apimPublisherName
  }
}

// Scenario: per-team subscription key -> rate limit + weekly quota -> the API
// forwards to the Foundry model, authenticated by APIM's managed identity.
resource apimProduct 'Microsoft.ApiManagement/service/products@2023-05-01-preview' = if (deployApim) {
  parent: apim
  name: 'ai-team'
  properties: {
    displayName: 'AI Team'
    description: 'Governed model access for a team: rate-limited, quota-capped.'
    subscriptionRequired: true
    approvalRequired: false
    state: 'published'
  }
}

resource apimProductPolicy 'Microsoft.ApiManagement/service/products/policies@2023-05-01-preview' = if (deployApim) {
  parent: apimProduct
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: '<policies><inbound><rate-limit calls="60" renewal-period="60" /><quota calls="10000" renewal-period="604800" /></inbound><backend><forward-request /></backend><outbound /><on-error /></policies>'
  }
}

resource apimApi 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = if (deployApim) {
  parent: apim
  name: 'foundry'
  properties: {
    displayName: 'Foundry Model'
    path: 'openai'
    protocols: [ 'https' ]
    serviceUrl: 'https://takemyjob.services.ai.azure.com/api/projects/proj-default/openai/v1'
    subscriptionRequired: true
  }
}

resource apimApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-05-01-preview' = if (deployApim) {
  parent: apimApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: '<policies><inbound><base /><authentication-managed-identity resource="https://ai.azure.com" /><rate-limit-by-key calls="30" renewal-period="60" counter-key="@(context.Subscription.Id)" /><azure-openai-token-limit counter-key="@(context.Subscription.Id)" tokens-per-minute="10000" estimate-prompt-tokens="true" remaining-tokens-header-name="x-ratelimit-remaining-tokens" tokens-consumed-header-name="x-ratelimit-consumed-tokens" /></inbound><backend><forward-request /></backend><outbound /><on-error /></policies>'
  }
}

resource apimProductApi 'Microsoft.ApiManagement/service/products/apis@2023-05-01-preview' = if (deployApim) {
  parent: apimProduct
  name: apimApi.name
}

resource apimSub 'Microsoft.ApiManagement/service/subscriptions@2023-05-01-preview' = if (deployApim) {
  parent: apim
  name: 'team-alpha'
  properties: {
    displayName: 'Team Alpha'
    scope: apimProduct.id
    state: 'active'
  }
}

// APIM's managed identity calls the model on the caller's behalf.
resource raApimAi 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployApim) {
  name: guid(aiServices.id, apimName, 'apim-cog-user')
  scope: aiServices
  properties: {
    principalId: deployApim ? apim.identity.principalId : ''
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
  }
}

// ---------------------------------------------------------------------------
// Networking - VNet + private endpoints (AI Services, Cosmos, Search)
// ---------------------------------------------------------------------------
resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: { addressPrefixes: [ '10.20.0.0/16' ] }
    subnets: [
      {
        name: 'snet-aca'
        properties: {
          addressPrefix: '10.20.0.0/23'
          delegations: [
            { name: 'aca', properties: { serviceName: 'Microsoft.App/environments' } }
          ]
        }
      }
      {
        name: 'snet-pe'
        properties: {
          addressPrefix: '10.20.4.0/24'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource dnsZones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for z in peZones: {
  name: z
  location: 'global'
}]

resource dnsLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [for z in peZones: {
  name: '${z}/${namePrefix}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnet.id }
  }
  dependsOn: [ dnsZones ]
}]

resource peAi 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${namePrefix}-pe-ai'
  location: location
  properties: {
    subnet: { id: '${vnet.id}/subnets/snet-pe' }
    privateLinkServiceConnections: [
      {
        name: 'ai'
        properties: { privateLinkServiceId: aiServices.id, groupIds: [ 'account' ] }
      }
    ]
  }
}

resource peAiDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: peAi
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'cognitiveservices', properties: { privateDnsZoneId: resourceId('Microsoft.Network/privateDnsZones', 'privatelink.cognitiveservices.azure.com') } }
      { name: 'openai', properties: { privateDnsZoneId: resourceId('Microsoft.Network/privateDnsZones', 'privatelink.openai.azure.com') } }
      { name: 'servicesai', properties: { privateDnsZoneId: resourceId('Microsoft.Network/privateDnsZones', 'privatelink.services.ai.azure.com') } }
    ]
  }
  dependsOn: [ dnsZones ]
}

resource peCosmos 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${namePrefix}-pe-cosmos'
  location: location
  properties: {
    subnet: { id: '${vnet.id}/subnets/snet-pe' }
    privateLinkServiceConnections: [
      {
        name: 'cosmos'
        properties: { privateLinkServiceId: cosmos.id, groupIds: [ 'Sql' ] }
      }
    ]
  }
}

resource peCosmosDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: peCosmos
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'documents', properties: { privateDnsZoneId: resourceId('Microsoft.Network/privateDnsZones', 'privatelink.documents.azure.com') } }
    ]
  }
  dependsOn: [ dnsZones ]
}

resource peSearch 'Microsoft.Network/privateEndpoints@2023-11-01' = if (deployAiSearch) {
  name: '${namePrefix}-pe-search'
  location: location
  properties: {
    subnet: { id: '${vnet.id}/subnets/snet-pe' }
    privateLinkServiceConnections: [
      {
        name: 'search'
        properties: { privateLinkServiceId: search.id, groupIds: [ 'searchService' ] }
      }
    ]
  }
}

resource peSearchDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = if (deployAiSearch) {
  parent: peSearch
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'search', properties: { privateDnsZoneId: resourceId('Microsoft.Network/privateDnsZones', 'privatelink.search.windows.net') } }
    ]
  }
  dependsOn: [ dnsZones ]
}

// ---------------------------------------------------------------------------
// RBAC - least-privilege access for the harness identity
// ---------------------------------------------------------------------------
// Key Vault Secrets User
resource raKvSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, uami.id, 'kv-secrets-user')
  scope: keyVault
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}

// Cognitive Services User (call the model)
resource raAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, uami.id, 'cog-services-user')
  scope: aiServices
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
  }
}

// Search Index Data Contributor (write/read the vector index)
resource raSearchData 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAiSearch) {
  name: guid(searchName, uami.id, 'search-index-data')
  scope: search
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
  }
}

// Cosmos DB built-in data contributor (data-plane; local auth disabled)
resource raCosmosData 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmos
  name: guid(cosmos.id, uami.id, 'cosmos-data-contrib')
  properties: {
    principalId: uami.properties.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    scope: cosmos.id
  }
}

// Storage Blob Data Owner + Queue Data Contributor (Functions runtime uses AAD)
resource raStorageBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployFunctions) {
  name: guid(storage.id, uami.id, 'blob-data-owner')
  scope: storage
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
  }
}

resource raStorageQueue 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployFunctions) {
  name: guid(storage.id, uami.id, 'queue-data-contrib')
  scope: storage
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
  }
}

// Search Service Contributor (create/manage indexes via AAD, no keys)
resource raSearchService 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployAiSearch) {
  name: guid(searchName, uami.id, 'search-service-contrib')
  scope: search
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
  }
}

// AcrPull (orchestrator identity pulls its image)
resource raAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, uami.id, 'acr-pull')
  scope: acr
  properties: {
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
output orchestratorFqdn string = orchestrator.properties.configuration.ingress.fqdn
output managedIdentityClientId string = uami.properties.clientId
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output searchEndpoint string = deployAiSearch ? 'https://${searchName}.search.windows.net' : 'not-deployed'
output aiServicesEndpoint string = aiServices.properties.endpoint
output vnetId string = vnet.id
output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output apimGatewayUrl string = deployApim ? apim.properties.gatewayUrl : 'not-deployed'
