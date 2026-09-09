_cache = {}

def get_config(key):
    if key not in _cache:
        _cache[key] = db.query(Config).filter(Config.key == key).first()
    return _cache[key]
