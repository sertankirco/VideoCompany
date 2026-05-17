"""
Growlabs Simüle Canlı Maç Verisi Üretici

Gerçek bir turnuva API'si olmadan render motorunu beslemek için kullanılır.
Her 30 saniyede bir gerçekçi bir maç eventi üretir ve RenderEngine kuyruğuna ekler.

Ayrıca ham maç verisini Growlabs Kurgu Anayasası standartlarına uygun,
agresif ve merak uyandıran 3 saniyelik "Hook" metnine dönüştürür.
"""

import logging
import random
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.render_engine import RenderEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Turnuva senaryo havuzu
# ---------------------------------------------------------------------------

_MATCH_POOL = [
    {
        "match_id": "WC2026_FRA_BRA",
        "team_home": "Fransa",
        "team_away": "Brezilya",
        "players_home": ["Mbappe", "Griezmann", "Dembele", "Giroud"],
        "players_away": ["Vinicius Jr", "Rodrygo", "Neymar", "Endrick"],
    },
    {
        "match_id": "WC2026_TUR_ARG",
        "team_home": "Türkiye",
        "team_away": "Arjantin",
        "players_home": ["Arda Güler", "Calhanoglu", "Yildiz", "Aktürkoglu"],
        "players_away": ["Messi", "Di Maria", "Alvarez", "Mac Allister"],
    },
    {
        "match_id": "WC2026_ESP_GER",
        "team_home": "İspanya",
        "team_away": "Almanya",
        "players_home": ["Pedri", "Yamal", "Morata", "Olmo"],
        "players_away": ["Musiala", "Wirtz", "Havertz", "Gnabry"],
    },
    {
        "match_id": "WC2026_POR_NED",
        "team_home": "Portekiz",
        "team_away": "Hollanda",
        "players_home": ["Ronaldo", "Bruno Fernandes", "Felix", "Leao"],
        "players_away": ["Gakpo", "Depay", "Dumfries", "De Ligt"],
    },
    {
        "match_id": "WC2026_ENG_JPN",
        "team_home": "İngiltere",
        "team_away": "Japonya",
        "players_home": ["Bellingham", "Saka", "Kane", "Foden"],
        "players_away": ["Kubo", "Mitoma", "Doan", "Kamada"],
    },
]

# Event türleri ve ağırlıkları (GOAL %40 dominant, diğerleri %60 paylaşır)
_EVENT_WEIGHTS = {
    "GOAL":        0.40,
    "YELLOW_CARD": 0.25,
    "RED_CARD":    0.05,
    "HALFTIME":    0.15,
    "FULLTIME":    0.15,
}

# ---------------------------------------------------------------------------
# Hook metin şablonları (Growlabs Kurgu Anayasası)
# Kural: max 3 satır, 3 saniyede okunabilir, agresif + merak uyandırıcı
# ---------------------------------------------------------------------------

_HOOK_TEMPLATES: dict[str, list[str]] = {
    "GOAL": [
        "BU ANIN TADINI ÇIKARIN...\n{player} VURDU!\n{team_home} {score}",
        "{minute}. DAKİKA. TARİH YAZILIYOR.\n{player} — {team_home}\nSKOR: {score}",
        "KİMSE BUNU BEKLEMİYORDU.\n{player} GİRDİ!\n{score} • {minute}'",
        "FRANSA DEĞİL, {team_home}!\n{player} DEVİRDİ.\n{score} — DUR BAKAYIM",
    ],
    "YELLOW_CARD": [
        "HAKEM ÇEKTİ!\n{player} SARI GÖRDÜ.\n{minute}. DAKİKA — {team}",
        "TARTIŞMALI AN!\n{player} ({team})\nSARI KART • {minute}'",
        "{minute}'. {team} SALLANMAYA BAŞLADI.\n{player}'A SARI.\nSONRAKİ ADIM NE OLACAK?",
    ],
    "RED_CARD": [
        "OYUN DEĞİŞTİ.\n{player} GİTTİ.\n{team} 10 KİŞİ • {minute}'",
        "SKANDAL!\n{player} KIRMIZI GÖRDÜ.\n{team} ZOR DURUMDA",
        "{minute}'. KAOS.\n{player} DIŞARI.\n{team}: 10 KİŞİ — KATLAN!",
    ],
    "HALFTIME": [
        "DEVRE ARASI.\nSKOR: {score}\nİKİNCİ YARIDA NE OLACAK?",
        "45 DAKİKA BİTTİ.\n{team_home} VS {team_away}\n{score} — BEKLE",
        "SOYUNMA ODASI KONUŞUYOR.\nSKOR: {score}\nSENİN TAHMİNİN NE?",
    ],
    "FULLTIME": [
        "MAÇ SONA ERDİ!\n{team_home} {score} {team_away}\nTARİH YAZILDI.",
        "FİNAL!\n{score}\n2026 DÜNYA KUPASI'NDA YENİ SAYFA",
        "{team_home} KAZANDI MI?\nSKOR: {score}\nBU ANIN TADINI ÇIKARIN.",
    ],
}

_DEFAULT_HOOK = "{event_label}!\n{team_home} VS {team_away}\n{score} • {minute}'"


def generate_hook_text(event_data: dict) -> str:
    """
    Ham maç event verisini Growlabs Kurgu Anayasası hook metnine dönüştürür.

    Kurallar:
    - Maksimum 3 satır (60fps'de 180 frame = 3s içinde okunabilir)
    - 1. satır: merak tetikleyici ifade veya agresif soru
    - 2. satır: duygusal yoğunluk (isim, dakika, takım)
    - 3. satır: kısa, yumruk etkisi (skor)

    Args:
        event_data: MatchEvent dict'i (event_type, player_name, score_home, vb.)

    Returns:
        3 satıra kadar yeni satır karakteriyle ayrılmış hook metni.
    """
    event_type = event_data.get("event_type", "GOAL")
    player = event_data.get("player_name") or "OYUNCU"
    team_home = event_data.get("team_home", "EV SAHİBİ")
    team_away = event_data.get("team_away", "MİSAFİR")
    team = event_data.get("team", team_home)
    score_home = event_data.get("score_home", 0)
    score_away = event_data.get("score_away", 0)
    score = f"{score_home}-{score_away}"
    minute = str(event_data.get("minute", "?"))
    event_label = {
        "GOAL": "GOL",
        "YELLOW_CARD": "SARI KART",
        "RED_CARD": "KIRMIZI KART",
        "HALFTIME": "DEVRE ARASI",
        "FULLTIME": "MAÇ SONU",
    }.get(event_type, event_type)

    ctx = {
        "player": player.upper(),
        "team_home": team_home.upper(),
        "team_away": team_away.upper(),
        "team": team.upper(),
        "score": score,
        "minute": minute,
        "event_label": event_label,
    }

    templates = _HOOK_TEMPLATES.get(event_type)
    if templates:
        template = random.choice(templates)
    else:
        template = _DEFAULT_HOOK

    try:
        hook = template.format(**ctx)
    except KeyError:
        hook = _DEFAULT_HOOK.format(**ctx)

    # Anayasa güvencesi: 3 satırı aşma
    lines = hook.split("\n")
    if len(lines) > 3:
        hook = "\n".join(lines[:3])

    return hook


# ---------------------------------------------------------------------------
# Simülatör
# ---------------------------------------------------------------------------


class MatchDataSimulator:
    """
    30 saniyede bir gerçekçi Dünya Kupası eventi üretir ve RenderEngine'e iletir.

    Simülatör bir maç senaryosunu takip eder:
    - Dakika 0'dan başlar, her event'te ilerler
    - GOAL'da skor artar
    - Dakika 45 geçince HALFTIME gönderilir
    - Dakika 90 geçince FULLTIME gönderilir, yeni maç başlar
    """

    def __init__(self, engine: "RenderEngine", interval_sec: int = 30):
        self.engine = engine
        self.interval_sec = interval_sec
        self._running = False
        self._thread: threading.Thread | None = None

        # Aktif maç durumu
        self._current_match: dict = random.choice(_MATCH_POOL).copy()
        self._minute: int = 0
        self._score_home: int = 0
        self._score_away: int = 0
        self._halftime_sent: bool = False

    def start(self) -> None:
        """Arka planda event simülasyonunu başlatır."""
        if self._running:
            logger.warning("Simulator already running.")
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            "MatchDataSimulator started — interval=%ds, match=%s vs %s",
            self.interval_sec,
            self._current_match["team_home"],
            self._current_match["team_away"],
        )

    def stop(self) -> None:
        """Simülasyonu durdurur."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("MatchDataSimulator stopped.")

    def emit_single(self, event_type: str = "GOAL") -> dict:
        """Test veya CLI kullanımı için tek event üretir ve engine'e gönderir."""
        event = self._build_event(event_type)
        self.engine.submit(event)
        logger.info("Single event emitted: %s", event_type)
        return event

    def _loop(self) -> None:
        while self._running:
            try:
                event = self._next_event()
                self.engine.submit(event)
                logger.info(
                    "[simulator] %s | %s | min=%s | score=%d-%d",
                    event["match_id"],
                    event["event_type"],
                    event["minute"],
                    event["score_home"],
                    event["score_away"],
                )
            except Exception as exc:
                logger.exception("Simulator event error: %s", exc)

            time.sleep(self.interval_sec)

    def _next_event(self) -> dict:
        """Mevcut maç durumuna göre sonraki eventi üretir."""
        self._minute += random.randint(2, 8)  # Gerçekçi dakika ilerlemesi

        # Devre arası kontrolü
        if self._minute >= 45 and not self._halftime_sent:
            self._halftime_sent = True
            self._minute = 45
            return self._build_event("HALFTIME")

        # Maç sonu kontrolü
        if self._minute >= 92:
            event = self._build_event("FULLTIME")
            self._reset_match()
            return event

        # Normal event seçimi
        event_type = self._weighted_choice(_EVENT_WEIGHTS)
        if event_type == "GOAL":
            if random.random() < 0.55:
                self._score_home += 1
            else:
                self._score_away += 1

        return self._build_event(event_type)

    def _build_event(self, event_type: str) -> dict:
        """Verilen event_type için tam event dict'i oluşturur."""
        match = self._current_match
        is_home_team = random.random() < 0.5
        team = match["team_home"] if is_home_team else match["team_away"]
        players_key = "players_home" if is_home_team else "players_away"
        player = random.choice(match.get(players_key, ["OYUNCU"]))

        minute_str = str(self._minute)
        if self._minute > 90:
            minute_str = f"90+{self._minute - 90}"

        return {
            "match_id": f"{match['match_id']}_{datetime.utcnow().strftime('%H%M%S')}",
            "event_type": event_type,
            "minute": self._minute,
            "team": team,
            "player_name": player if event_type in ("GOAL", "YELLOW_CARD", "RED_CARD") else None,
            "score_home": self._score_home,
            "score_away": self._score_away,
            "team_home": match["team_home"],
            "team_away": match["team_away"],
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _reset_match(self) -> None:
        """Maç bittikten sonra yeni maç seçer ve sayaçları sıfırlar."""
        self._current_match = random.choice(_MATCH_POOL).copy()
        self._minute = 0
        self._score_home = 0
        self._score_away = 0
        self._halftime_sent = False
        logger.info(
            "[simulator] New match: %s vs %s",
            self._current_match["team_home"],
            self._current_match["team_away"],
        )

    @staticmethod
    def _weighted_choice(weights: dict[str, float]) -> str:
        """Ağırlıklı rastgele seçim yapar."""
        keys = list(weights.keys())
        vals = list(weights.values())
        return random.choices(keys, weights=vals, k=1)[0]
