import os
import json
import requests
import instaloader
from instaloader import Profile, Post

# Загрузка конфигурации из окружения
INSTAGRAM_ACCOUNTS = os.getenv("ACCOUNTS", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
IG_USERNAME = os.getenv("IG_USERNAME")
IG_PASSWORD = os.getenv("IG_PASSWORD")

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID or not INSTAGRAM_ACCOUNTS:
    raise RuntimeError("Необходимо задать переменные окружения: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ACCOUNTS.")

accounts = [user.strip() for user in INSTAGRAM_ACCOUNTS.replace(",", " ").split()]
print(f"[INFO] Monitoring Instagram accounts: {accounts}")

# Инициализация Instaloader
L = instaloader.Instaloader()

# Если указаны учетные данные Instagram, пробуем залогиниться
session_file = None
if IG_USERNAME and IG_PASSWORD:
    session_file = f"{IG_USERNAME}.session"
    try:
        # Попытка загрузить ранее сохраненную сессию, чтобы не логиниться заново
        L.load_session_from_file(IG_USERNAME, filename=session_file)
        print("[INFO] Instagram session loaded from file.")
    except Exception as e:
        print(f"[INFO] No valid session found. Logging in as {IG_USERNAME}...")
        L.login(IG_USERNAME, IG_PASSWORD)
        # Сохранение сессии в файл для последующего использования
        L.save_session_to_file(filename=session_file)
        print("[INFO] Logged in and session saved.")

# Загрузка состояния (последние обработанные посты) из файла
state = {}
state_path = "state.json"
if os.path.exists(state_path):
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except json.JSONDecodeError:
        state = {}
else:
    state = {}

# Функция отправки сообщения в Telegram (медиагруппа или одиночное сообщение)
def send_telegram_media(caption, media_group):
    """Отправляет либо медиагруппу (альбом) либо одиночное фото/видео с подписью."""
    url_base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    if len(media_group) > 1:
        # Формируем медиагруппу (до 10 элементов). Caption только у первого элемента.
        # media_group — список словарей типа {"type": "photo"/"video", "media": url, "caption": "..."}.
        media_group[0]["caption"] = caption
        media_group[0]["parse_mode"] = "HTML"  # разрешаем HTML-разметку, если нужна
        # Отправка альбома
        resp = requests.post(f"{url_base}/sendMediaGroup", json={
            "chat_id": TELEGRAM_CHAT_ID,
            "media": media_group
        })
    else:
        # Отправка одиночного элемента с подписью
        media = media_group[0]
        media_type = media["type"]
        if media_type == "photo":
            resp = requests.post(f"{url_base}/sendPhoto", data={
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": media["media"],
                "caption": caption,
                "parse_mode": "HTML"
            })
        elif media_type == "video":
            resp = requests.post(f"{url_base}/sendVideo", data={
                "chat_id": TELEGRAM_CHAT_ID,
                "video": media["media"],
                "caption": caption,
                "parse_mode": "HTML"
            })
        else:
            return  # unknown type
    # Логируем результат или ошибки
    try:
        result = resp.json()
    except Exception as e:
        print(f"[ERROR] Telegram API response error: {e}")
        return
    if not result.get("ok"):
        print(f"[ERROR] Failed to send message: {result}")

# Основной процесс: проверка каждого аккаунта
for username in accounts:
    print(f"[INFO] Checking new posts for Instagram account: {username}")
    try:
        profile = Profile.from_username(L.context, username)
    except Exception as e:
        print(f"[ERROR] Could not load profile {username}: {e}")
        continue

    last_post_id = state.get(username)
    new_last_post_id = last_post_id  # будет обновлено на самый свежий пост
    sent_count = 0

    for post in profile.get_posts():
        post_id = post.mediaid  # уникальный ID (числовой) поста
        if last_post_id is not None and post_id == last_post_id:
            # Дошли до уже обработанного поста – прерываем цикл, дальше только старее
            break
        # Этот пост более новый, чем сохраненный последний - нужно отправить
        if new_last_post_id is None or post_id > new_last_post_id:
            new_last_post_id = post_id  # обновляем самый новый пост (может обновиться несколько раз в первом цикле)

        # Формируем подпись: можно включить имя аккаунта и оригинальный текст
        caption_text = post.caption or ""
        # Добавим пометку источника (имя аккаунта)
        caption = f"<b>Instagram: @{username}</b>\n{caption_text}"
        if len(caption) > 1024:
            caption = caption[:1020] + "..."  # Telegram ограничение 1024 символа на подпись

        # Собираем медиа:
        media_items = []
        if post.mediacount == 1:
            # Один медиафайл
            if post.is_video:
                media_items.append({"type": "video", "media": post.video_url})
            else:
                media_items.append({"type": "photo", "media": post.url})
        else:
            # Несколько фото/видео (sidecar)
            for idx, res in enumerate(post.get_sidecar_nodes()):
                if res.is_video:
                    media_items.append({"type": "video", "media": res.video_url})
                else:
                    media_items.append({"type": "photo", "media": res.display_url})
                # Telegram media group максимум 10 элементов
                if idx >= 9:
                    break

        # Отправляем в Telegram: если в посте несколько фото/видео, отправляем как альбом
        send_telegram_media(caption, media_items)
        sent_count += 1

    # Обновляем сохраненное последнее сообщение для профиля (если нашли более новый)
    if new_last_post_id and new_last_post_id != last_post_id:
        state[username] = new_last_post_id
        print(f"[INFO] Updated last post ID for {username}: {new_last_post_id}")
    else:
        print(f"[INFO] No new posts for {username}")

    # (Опционально) пауза между аккаунтами, чтобы снизить нагрузку на Instagram.
    # time.sleep(2)

# Сохранение состояния в файл
with open(state_path, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False)

print("[INFO] Script completed. Posts sent:", sum(1 for _ in state))
