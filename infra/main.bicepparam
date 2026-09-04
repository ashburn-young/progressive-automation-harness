using './main.bicep'

// Reuse what already exists in rg-takemyjob.
param existingAiServicesName = 'takemyjob'
param existingAppInsightsName = 'airgtakemyjob03b5ad'
param existingKeyVaultName = 'kvrgtakemyjob03b5ad'

param namePrefix = 'pah'

// Foundation + memory (AI Search) with private networking. APIM/Redis stay off
// (APIM is the priciest/slowest; Cosmos covers session state without Redis).
param deployFunctions = true
param deployAiSearch = true
param deployRedis = false
param deployApim = true

// Data layer is private-only now: the VNet-integrated orchestrator reaches
// Cosmos/Search over private endpoints. Local dev uses files, so it is unaffected.
param lockDownPublicAccess = true
