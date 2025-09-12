def create_user_ids_set(env: str = "") -> set[int]:
    user_ids = set(sorted([int(x) for x in ' '.join(env.split(',')).split()])) if env else set()
    return user_ids
