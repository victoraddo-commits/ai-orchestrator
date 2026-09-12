class VaultManager:
    _instance = None
    _instance_lock = Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super(VaultManager, cls).__new__(cls)
        return cls._instance

    def get_instance(self):
        return self._instance

# Example usage
if __name__ == '__main__':
    vault_manager = VaultManager()
    vault_manager_instance = vault_manager.get_instance()
    print(vault_manager_instance)
