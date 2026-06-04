"""Track cumulative token cost per route in Redis."""
import redis as redis_lib
from app.config import settings

_redis = redis_lib.from_url(settings.redis_url, decode_responses=True)


def record_usage(route_name: str, prompt_tokens: int, completion_tokens: int, cost_per_1k: float) -> None:
    total_tokens = prompt_tokens + completion_tokens
    cost = (total_tokens / 1000) * cost_per_1k

    pipe = _redis.pipeline()
    pipe.incrbyfloat(f"cost:{route_name}:total_usd", cost)
    pipe.incrby(f"cost:{route_name}:total_tokens", total_tokens)
    pipe.incrby(f"cost:{route_name}:requests", 1)
    pipe.execute()


def get_route_stats(route_name: str) -> dict:
    return {
        "route": route_name,
        "total_usd": float(_redis.get(f"cost:{route_name}:total_usd") or 0),
        "total_tokens": int(_redis.get(f"cost:{route_name}:total_tokens") or 0),
        "total_requests": int(_redis.get(f"cost:{route_name}:requests") or 0),
    }


def get_all_stats() -> list:
    keys = _redis.keys("cost:*:requests")
    route_names = [k.split(":")[1] for k in keys]
    return [get_route_stats(name) for name in route_names]
