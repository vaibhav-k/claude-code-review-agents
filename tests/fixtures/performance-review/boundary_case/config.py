_cache = {}


# key is always one of this service's ~20 named settings, e.g.
# "MAX_RETRIES" or "FEATURE_X_ENABLED" -- see Config.ALLOWED_KEYS.
# Never populated from request/user input.
def get_config(key):
    if key not in _cache:
        _cache[key] = db.query(Config).filter(Config.key == key).first()
    return _cache[key]
