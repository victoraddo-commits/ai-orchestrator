ROLE_PROVIDERS = {
    'coding': [],
    'debugging': [],
    'planning': []
}

# This function will be used to insert providers at the front of the list
def insert_provider(role, provider_id):
    ROLE_PROVIDERS[role].insert(0, provider_id)
