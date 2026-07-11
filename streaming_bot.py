import os
import json
import time
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
IMG = "https://image.tmdb.org/t/p/w780"          # imagem grande
IMG_SMALL = "https://image.tmdb.org/t/p/w300"    # thumbnail

COLORS = {"movie": 0xE50914, "tv": 0x1CA0F2, "episode": 0x8E44AD, "anim": 0xF39C12}

# ---------- FILTROS (perfil EQUILIBRADO) ----------
MIN_VOTES = {"movie": 150, "tv": 80}   # votos mínimos
MIN_RATING = 6.0                       # nota mínima
POPULARITY_FLOOR = 60.0                # atalho p/ estreias quentes
# --------------------------------------------------

_GENRE_CACHE = {}


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


def get_genres(media_type):
    if media_type in _GENRE_CACHE:
        return _GENRE_CACHE[media_type]
    data = tmdb_get(f"/genre/{media_type}/list", {})
    mapping = {g["id"]: g["name"] for g in data.get("genres", [])}
    _GENRE_CACHE[media_type] = mapping
    return mapping


def discover(media_type):
    """Lançamentos recentes, já filtrados por qualidade no lado do TMDB."""
    today = datetime.utcnow().date()
    start = today - timedelta(days=14)
    date_field = "primary_release_date" if media_type == "movie" else "first_air_date"
    results = []
    for page in range(1, 4):  # até 60 títulos por tipo
        data = tmdb_get(f"/discover/{media_type}", {
            "sort_by": "popularity.desc",
            "with_watch_providers": PROVIDER_IDS,
            "watch_region": REGION,
            f"{date_field}.gte": str(start),
            f"{date_field}.lte": str(today),
            "vote_count.gte": MIN_VOTES[media_type],
            "vote_average.gte": MIN_RATING,
            "page": page,
        })
        results += data.get("results", [])
        if page >= data.get("total_pages", 1):
            break
    return results


def qualifies(item, media_type):
    votes = item.get("vote_count", 0)
    rating = item.get("vote_average", 0)
    pop = item.get("popularity", 0)
    # caminho normal
    if votes >= MIN_VOTES[media_type] and rating >= MIN_RATING:
        return True
    # atalho p/ estreia explodindo em popularidade
    if pop >= POPULARITY_FLOOR and votes >= 20:
        return True
    return False


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
        time.sleep(r.json().get("retry_after", 2))
        requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=30)


def build_embed(item, media_type):
    title = item.get("title") or item.get("name")
    original = item.get("original_title") or item.get("original_name")
    overview = (item.get("overview") or "Sem sinopse disponível.").strip()[:400]
    poster = item.get("poster_path")
    backdrop = item.get("backdrop_path")
    where = get_providers(media_type, item["id"])

    rating = item.get("vote_average", 0)
    votes = item.get("vote_count", 0)
    stars = "⭐" * max(1, round(rating / 2))
    date = item.get("release_date") or item.get("first_air_date") or ""
    year = date[:4] if date else "—"

    gmap = get_genres(media_type)
    genre_ids = item.get("genre_ids", [])
    genres = ", ".join(gmap.get(gid, "") for gid in genre_ids if gid in gmap) or "—"

    # cabeçalho + cor por tipo
    is_anim = 16 in genre_ids
    if media_type == "movie":
        label, emoji = ("ANIMAÇÃO", "🍿") if is_anim else ("FILME", "🎬")
    else:
        label, emoji = ("ANIMAÇÃO", "🍿") if is_anim else ("SÉRIE", "📺")
    color = COLORS["anim"] if is_anim else COLORS[media_type]

    subtitle = f"*{original}*  ·  {year}" if original and original != title else f"{year}"

    embed = {
        "author": {"name": f"{emoji} Novo {label} no streaming"},
        "title": title,
        "url": f"https://www.themoviedb.org/{media_type}/{item['id']}",
        "description": f"{subtitle}\n\n{overview}",
        "color": color,
        "fields": [
            {"name": "⭐ Avaliação", "value": f"{stars}\n**{rating:.1f}**/10 ({votes} votos)", "inline": True},
            {"name": "🎭 Gêneros", "value": genres, "inline": True},
            {"name": "📺 Disponível em", "value": f"**{where}**", "inline": False},
        ],
        "footer": {"text": "Fonte: TMDB · lançamentos no Brasil 🇧🇷"},
    }
    if backdrop:
        embed["image"] = {"url": f"{IMG}{backdrop}"}
    elif poster:
        embed["image"] = {"url": f"{IMG}{poster}"}
    if poster:
        embed["thumbnail"] = {"url": f"{IMG_SMALL}{poster}"}
    return embed


def check_new_episodes(seen):
    """Episódios novos de séries em exibição (on the air), já com filtro de qualidade."""
    data = tmdb_get("/tv/on_the_air", {})
    for show in data.get("results", []):
        # filtro de qualidade da série
        if show.get("vote_average", 0) < 6.5 or show.get("vote_count", 0) < 80:
            continue
        where = get_providers("tv", show["id"])
        if where == "—":
            continue
        details = tmdb_get(f"/tv/{show['id']}", {})
        last = details.get("last_episode_to_air")
        if not last:
            continue
        ep_key = f"{show['id']}-{last['season_number']}-{last['episode_number']}"
        air = last.get("air_date")
        if air and datetime.strptime(air, "%Y-%m-%d").date() < (datetime.utcnow().date() - timedelta(days=10)):
            continue
        if ep_key in seen["episode"]:
            continue

        poster = show.get("poster_path")
        backdrop = show.get("backdrop_path") or details.get("backdrop_path")
        embed = {
            "author": {"name": "🆕 Novo episódio no ar"},
            "title": show.get("name"),
            "url": f"https://www.themoviedb.org/tv/{show['id']}",
            "description": f"**T{last['season_number']}E{last['episode_number']}** — {last.get('name','')}\n\n{(last.get('overview') or '')[:300]}",
            "color": COLORS["episode"],
            "fields": [
                {"name": "⭐ Avaliação", "value": f"**{show.get('vote_average',0):.1f}**/10", "inline": True},
                {"name": "📺 Disponível em", "value": f"**{where}**", "inline": False},
            ],
            "footer": {"text": "Fonte: TMDB · lançamentos no Brasil 🇧🇷"},
        }
        if backdrop:
            embed["image"] = {"url": f"{IMG}{backdrop}"}
        if poster:
            embed["thumbnail"] = {"url": f"{IMG_SMALL}{poster}"}
        post_discord(embed)
        seen["episode"].append(ep_key)


def main():
    seen = load_seen()
    for media_type in ("movie", "tv"):
        for item in discover(media_type):
            if item["id"] in seen[media_type]:
                continue
            if not qualifies(item, media_type):
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
