def process_batch(items):
    try:
        return [transform(i) for i in items]
    except Exception as e:
        logger.error(f"batch failed: {e}")
        raise
