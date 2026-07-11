import os
import json
import requests
from datetime import datetime, timedelta

TMDB_TOKEN = os.environ["TMDB_TOKEN"]
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK"]

REGION = "BR"
LANG = "pt-BR"
SEEN_FILE = "seen.json"

# Provedores no Brasil (IDs do TMDB/JustWatch)
PROVIDERS = {
    8: "Netflix",
    9: "Prime Video",
    337: "Disney+",
    1899: "Max",          # HBO Max / Max
    350: "Apple TV+",
    531: "Paramount+",
    619: "Globoplay",
}
PROVIDER_IDS = "|".join(str(p) for p in PROVIDERS)

HEADERS = {"Authorization": f"Bearer {TMDB_TOKEN}", "accept": "application/json"}
BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w500"

COLORS = {"movie": 0xE50914, "tv": 0x1CA0F2, "episode": 0x8E44AD}


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return json.load(f)
    return {"movie": [], "tv": [], "episode": []}


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f)


def tmdb_get(path, params=None):
    p = {"language": LANG, "watch_region": REGION}
    if params:
        p.update(params)
    r = requests.get(f"{BASE}{path}", headers=HEADERS, params=p, timeout=30)
    r.raise_for_status()
    return r.json()


def discover(media_type):
    """Filmes/séries lançados nos últimos 14 dias em algum provedor."""
    today = datetime.utcnow().date()
    start = today - timedelta(days=14)
    date_field = "primary_release_date" if media_type == "movie" else "first_air_date"
    results = []
    for page in range(1, 4):  # 3 páginas = até 60 títulos por tipo
        data = tmdb_get(f"/discover/{media_type}", {
            "sort_by": f"{date_field}.desc",
            "with_watch_providers": PROVIDER_IDS,
            "watch_region": REGION,
            f"{date_field}.gte": str(start),
            f"{date_field}.lte": str(today),
            "page": page,
        })
        results += data.get("results", [])
        if page >= data.get("total_pages", 1):
            break
    return results


def get_providers(media_type, item_id):
    try:
        data = tmdb_get(f"/{media_type}/{item_id}/watch/providers", {})
        br = data.get("results", {}).get(REGION, {})
        names = set()
        for key in ("flatrate", "free", "ads"):
            for prov in br.get(key, []):
                names.add(prov["provider_name"])
        return ", ".join(sorted(names)) or "—"
    except Exception:
        return "—"


def post_discord(embed):
    r = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=30)
    if r.status_code == 429:  # rate limit
        import time
        time.sleep(r.json().get("retry_after", 2))
        requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=30)


def build_embed(item, media_type):
    title = item.get("title") or item.get("name")
    overview = (item.get("overview") or "Sem sinopse.")[:300]
    poster = item.get("poster_path")
    where = get_providers(media_type, item["id"])
    label = "🎬 Filme novo" if media_type == "movie" else "📺 Série nova"
    embed = {
        "title": f"{label}: {title}",
        "description": overview,
        "color": COLORS[media_type],
        "fields": [{"name": "Disponível em", "value": where, "inline": False}],
    }
    if poster:
        embed["thumbnail"] = {"url": f"{IMG}{poster}"}
    return embed


def check_new_episodes(seen):
    """Episódios novos de séries em exibição (on the air)."""
    data = tmdb_get("/tv/on_the_air", {})
    for show in data.get("results", []):
        # só séries que estão em algum dos nossos provedores
        where = get_providers("tv", show["id"])
        if where == "—":
            continue
        details = tmdb_get(f"/tv/{show['id']}", {})
        last = details.get("last_episode_to_air")
        if not last:
            continue
        ep_key = f"{show['id']}-{last['season_number']}-{last['episode_number']}"
        air = last.get("air_date")
        # só episódios dos últimos 10 dias
        if air and datetime.strptime(air, "%Y-%m-%d").date() < (datetime.utcnow().date() - timedelta(days=10)):
            continue
        if ep_key in seen["episode"]:
            continue
        embed = {
            "title": f"🆕 Novo episódio: {show.get('name')}",
            "description": f"**T{last['season_number']}E{last['episode_number']}** — {last.get('name','')}\n{(last.get('overview') or '')[:250]}",
            "color": COLORS["episode"],
            "fields": [{"name": "Disponível em", "value": where, "inline": False}],
        }
        poster = show.get("poster_path")
        if poster:
            embed["thumbnail"] = {"url": f"{IMG}{poster}"}
        post_discord(embed)
        seen["episode"].append(ep_key)


def main():
    seen = load_seen()
    for media_type in ("movie", "tv"):
        for item in discover(media_type):
            if item["id"] in seen[media_type]:
                continue
            if not (item.get("overview") or item.get("poster_path")):
                continue
            post_discord(build_embed(item, media_type))
            seen[media_type].append(item["id"])
    check_new_episodes(seen)
    # mantém a lista enxuta
    for k in seen:
        seen[k] = seen[k][-2000:]
    save_seen(seen)
    print("Concluído.")


if __name__ == "__main__":
    main()
