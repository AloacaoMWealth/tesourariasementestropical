import base64
import html
import io
import re
from difflib import SequenceMatcher
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import tomllib
except Exception:
    tomllib = None

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from PIL import Image

APP_TITLE = "Tesouraria Sementes Tropical"
SUBTITLE = "Gestão Profissional do Caixa Empresarial"
PARTNER = "Sementes Tropical"
GESTOR = "M Wealth"

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_POSITIONS_DIR = BASE_DIR / "data" / "positions"
CONFIG_DIR = BASE_DIR / "data" / "config"
CLIENT_CONFIG = CONFIG_DIR / "clientes.csv"
CLIENT_PROFILE = CONFIG_DIR / "cliente.toml"
CLIENT_LOGO = BASE_DIR / "data" / "assets" / "Logo-sementestropical.png"
BOTUVERA_LOGO = CLIENT_LOGO  # compatibilidade com o restante do app
MWEALTH_LOGO = BASE_DIR / "mwealth-light.png"
FUND_APPLICATIONS_FILE = CONFIG_DIR / "aplicacoes_fundos.xlsx"
FUND_APPLICATIONS_FALLBACK = BASE_DIR / "Aplicações Fundos de Investimentos Botuverá.xlsx"
FUND_MAPPING_FILE = CONFIG_DIR / "fundos_mapeamento.xlsx"
POLICY_FILE = CONFIG_DIR / "politica_investimentos.xlsx"

MIN_POS_FIXADO = 0.80
VALIDACAO_CFO_VALOR = 5_000_000
LIMITE_EMISSOR_VALOR = 10_000_000
LIMITE_EMISSOR_PCT = 0.50

LIQUIDITY_ORDER = ["D+0", "D+1", "D+2", "D+3", "D+4", "D+5", "D+30", "D+31", "D+60", "D+90", "D+120", "D+180", "D+181", "N/A"]

# IOF regressivo para fundos: dias corridos.
# Dia 1 começa em 96%; no 30º dia a alíquota zera.
IOF_TABLE = {
    1: 96,
    2: 93,
    3: 90,
    4: 86,
    5: 83,
    6: 80,
    7: 76,
    8: 73,
    9: 70,
    10: 66,
    11: 63,
    12: 60,
    13: 56,
    14: 53,
    15: 50,
    16: 46,
    17: 43,
    18: 40,
    19: 36,
    20: 33,
    21: 30,
    22: 26,
    23: 23,
    24: 20,
    25: 16,
    26: 13,
    27: 10,
    28: 6,
    29: 3,
    30: 0,
}


def parse_bool(value, default=False):
    s = normalize_text(value)
    if s in ["sim", "s", "true", "1", "yes"]:
        return True
    if s in ["nao", "não", "n", "false", "0", "no"]:
        return False
    return default


def load_client_profile():
    """Carrega dados do cliente sem precisar mexer no código.

    Arquivo opcional: data/config/cliente.toml
    Exemplo:
    [cliente]
    app_title = "Tesouraria Sementes Tropical"
    partner = "Sementes Tropical"
    gestor = "M Wealth"
    subtitle = "Gestão Profissional do Caixa Empresarial"
    logo = "data/assets/Logo-sementestropical.png"
    """
    global APP_TITLE, SUBTITLE, PARTNER, GESTOR, CLIENT_LOGO, BOTUVERA_LOGO
    if not CLIENT_PROFILE.exists() or tomllib is None:
        return
    try:
        data = tomllib.loads(CLIENT_PROFILE.read_text(encoding="utf-8"))
        cliente = data.get("cliente", {})
        APP_TITLE = cliente.get("app_title", APP_TITLE)
        SUBTITLE = cliente.get("subtitle", SUBTITLE)
        PARTNER = cliente.get("partner", PARTNER)
        GESTOR = cliente.get("gestor", GESTOR)
        logo_value = cliente.get("logo")
        if logo_value:
            logo_path = Path(str(logo_value))
            if not logo_path.is_absolute():
                logo_path = BASE_DIR / logo_path
            CLIENT_LOGO = logo_path
            BOTUVERA_LOGO = CLIENT_LOGO
    except Exception:
        pass


load_client_profile()



def brl(v: float) -> str:
    try:
        v = float(v or 0)
    except Exception:
        v = 0.0

    txt = f"R$ {v:,.2f}"
    return txt.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v: float) -> str:
    try:
        v = float(v or 0)
    except Exception:
        v = 0.0

    txt = f"{100 * v:,.2f}%"
    return txt.replace(",", "X").replace(".", ",").replace("X", ".")


def short_money(v: float) -> str:
    try:
        v = float(v or 0)
    except Exception:
        v = 0

    if abs(v) >= 1_000_000:
        return f"R$ {v / 1_000_000:,.2f} mi".replace(",", "X").replace(".", ",").replace("X", ".")

    return brl(v)


def safe_div(a, b):
    try:
        return 0.0 if not b else float(a) / float(b)
    except Exception:
        return 0.0


def normalize_text(s) -> str:
    s = str(s or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("á", "a").replace("à", "a").replace("ã", "a").replace("â", "a")
    s = s.replace("é", "e").replace("ê", "e")
    s = s.replace("í", "i")
    s = s.replace("ó", "o").replace("ô", "o").replace("õ", "o")
    s = s.replace("ú", "u").replace("ç", "c")
    return s


def slugify(s) -> str:
    s = normalize_text(s)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "arquivo"


def parse_money(x) -> float:
    if x is None:
        return 0.0

    if isinstance(x, (int, float, np.number)):
        try:
            if pd.isna(x):
                return 0.0
        except Exception:
            pass
        return float(x)

    try:
        if pd.isna(x):
            return 0.0
    except Exception:
        pass

    s = str(x).strip()
    if s in ["", "-", "—", "nan", "None", "NaT"]:
        return 0.0

    s = s.replace("R$", "").replace("%", "").strip()
    s = s.replace(" ", "")

    # Formato brasileiro: 1.234.567,89
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    # Formato internacional vindo do Excel como texto: 506266.45
    elif s.count(".") == 1:
        left, right = s.split(".", 1)
        if len(right) in (1, 2):
            s = s
        else:
            s = s.replace(".", "")
    # Formato com separador de milhar: 1.234.567
    elif s.count(".") > 1:
        s = s.replace(".", "")

    try:
        return float(s)
    except Exception:
        return 0.0


def parse_date_br(x):
    if x is None:
        return pd.NaT

    try:
        if pd.isna(x):
            return pd.NaT
    except Exception:
        pass

    if isinstance(x, (datetime, pd.Timestamp)):
        ts = pd.to_datetime(x, errors="coerce")
        return pd.NaT if pd.isna(ts) else ts.date()

    if isinstance(x, date):
        return x

    s = str(x).strip()
    if s in ["", "-", "—", "nan", "NaT", "None"]:
        return pd.NaT

    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass

    ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
    return pd.NaT if pd.isna(ts) else ts.date()


def parse_fund_movement_date(x):
    """Lê a data da planilha de movimentações dos fundos sem inverter ISO.
    Corrige casos como 2026-05-12, que não pode virar 05/12/2026.
    """
    if x is None:
        return pd.NaT

    try:
        if pd.isna(x):
            return pd.NaT
    except Exception:
        pass

    if isinstance(x, (datetime, pd.Timestamp)):
        ts = pd.to_datetime(x, errors="coerce")
        return pd.NaT if pd.isna(ts) else ts.date()

    if isinstance(x, date):
        return x

    s = str(x).strip()
    if s in ["", "-", "—", "nan", "NaT", "None"]:
        return pd.NaT

    # Excel/pandas pode entregar datas como 2026-05-12. Nesse caso é ano-mês-dia.
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}", s):
        ts = pd.to_datetime(s, errors="coerce", yearfirst=True)
        return pd.NaT if pd.isna(ts) else ts.date()

    # Formato brasileiro digitado manualmente.
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass

    ts = pd.to_datetime(s, errors="coerce", dayfirst=True)
    return pd.NaT if pd.isna(ts) else ts.date()


def fund_match_score(a: str, b: str) -> float:
    """Score simples para casar nomes de fundos escritos de formas diferentes."""
    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    if a in b or b in a:
        return 0.92

    tokens_a = {t for t in a.split() if len(t) > 2}
    tokens_b = {t for t in b.split() if len(t) > 2}

    common = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b) or 1
    token_score = common / union
    seq_score = SequenceMatcher(None, a, b).ratio()

    return max(token_score, seq_score)




def liquidity_to_days(liquidity) -> int:
    """Converte D+0, D+31, D+180, acima de D+180 etc. em número para ordenação."""
    s = normalize_text(liquidity).replace(" ", "")
    if not s or s in ["n/a", "na", "nan", "none", "-"]:
        return 99999
    if "acimaded+180" in s or "acimad+180" in s:
        return 181
    nums = re.findall(r"d\+?(\d+)", s)
    if nums:
        try:
            return int(nums[-1])
        except Exception:
            return 99999
    nums = re.findall(r"(\d+)", s)
    if nums:
        try:
            return int(nums[-1])
        except Exception:
            return 99999
    return 99999


def canonical_liquidity(liquidity) -> str:
    d = liquidity_to_days(liquidity)
    if d == 99999:
        return "N/A"
    return f"D+{d}"


def fund_product_from_liquidity(liquidity) -> str:
    liq = canonical_liquidity(liquidity)
    return f"Fundos {liq}" if liq != "N/A" else "Fundos"


def days_until_date(target_date, reference_date: date):
    """Dias corridos entre a data de atualização e o vencimento.
    Usado para ordenar a renda fixa pelo que vence primeiro.
    """
    d = parse_date_br(target_date)
    if d is None:
        return np.nan
    try:
        if pd.isna(d):
            return np.nan
    except Exception:
        pass
    if not isinstance(d, date) or not isinstance(reference_date, date):
        return np.nan
    return (d - reference_date).days


def load_fund_mapping():
    """Lê data/config/fundos_mapeamento.xlsx.

    Colunas mínimas: Fundo, Liquidez.
    Coluna opcional: Alias. O Alias ajuda quando a XP abrevia ou muda o nome do fundo.
    """
    if not FUND_MAPPING_FILE.exists():
        return pd.DataFrame()
    try:
        df = pd.read_excel(FUND_MAPPING_FILE, dtype=str)
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    colmap = {normalize_text(c): c for c in df.columns}
    fundo_col = colmap.get("fundo")
    alias_col = colmap.get("alias")
    liquidez_col = colmap.get("liquidez") or colmap.get("prazo") or colmap.get("prazo de resgate")
    if not fundo_col or not liquidez_col:
        return pd.DataFrame()
    out = df[[fundo_col, liquidez_col] + ([alias_col] if alias_col else [])].copy()
    out = out.rename(columns={fundo_col: "fundo", liquidez_col: "liquidez"})
    if alias_col:
        out = out.rename(columns={alias_col: "alias"})
    else:
        out["alias"] = ""
    out["fundo"] = out["fundo"].astype(str).str.strip()
    out["alias"] = out["alias"].fillna("").astype(str).str.strip()
    out["liquidez"] = out["liquidez"].fillna("N/A").astype(str).str.strip()
    out["fundo_norm"] = out["fundo"].apply(normalize_text)
    out["alias_norm"] = out["alias"].apply(normalize_text)
    out["liq_dias"] = out["liquidez"].apply(liquidity_to_days)
    return out


def map_fund_liquidity(asset_name: str, group_text: str = "") -> str:
    mapping = load_fund_mapping()
    target = normalize_text(asset_name)
    base = normalize_text(f"{group_text} {asset_name}")

    if not mapping.empty:
        best_score = 0.0
        best_liq = None
        for _, row in mapping.iterrows():
            candidates = [row.get("fundo_norm", ""), row.get("alias_norm", "")]
            row_score = max(fund_match_score(target, c) for c in candidates if c)
            row_score = max(row_score, max((fund_match_score(base, c) for c in candidates if c), default=0.0))
            if row_score > best_score:
                best_score = row_score
                best_liq = row.get("liquidez")
        if best_liq and best_score >= 0.48:
            return canonical_liquidity(best_liq)

    # fallback: tenta usar prazo explícito no bloco/nome. Se não achar, assume D+0.
    explicit = re.findall(r"d\s*\+\s*(\d+)", f"{group_text} {asset_name}", flags=re.I)
    if explicit:
        return f"D+{int(explicit[-1])}"
    return "D+0"


def fmt_date_br(x) -> str:
    if x is None:
        return "—"

    try:
        if pd.isna(x):
            return "—"
    except Exception:
        pass

    try:
        return pd.to_datetime(x).strftime("%d/%m/%Y")
    except Exception:
        return "—"


def is_empty(x) -> bool:
    try:
        return pd.isna(x) or str(x).strip() == ""
    except Exception:
        return False


def logo_base64(path: Path) -> str:
    if not path.exists():
        return ""
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def iof_rate_by_days(days: int) -> int:
    try:
        d = int(days)
    except Exception:
        return 0

    # Para aplicação no mesmo dia, mostramos a primeira alíquota aplicável: 96%.
    if d <= 0:
        return 96

    if d >= 30:
        return 0

    return IOF_TABLE.get(d, 0)


def inject_css():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

        :root {
            color-scheme: dark !important;
            --bg-main: #03122b;
            --bg-panel: #0f1b31;
            --border-soft: rgba(148,163,184,.16);
            --text-main: #F8FAFC;
            --text-soft: #CBD5E1;
            --text-muted: #94A3B8;
            --accent: #8DB7FF;
            --accent-2: #9EC5FF;
            --green: #22C55E;
            --red: #F87171;
            --yellow: #FBBF24;
        }

        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif !important;
            color: var(--text-main) !important;
            background: var(--bg-main) !important;
        }

        .stApp {
            background:
                radial-gradient(circle at 12% 0%, rgba(59,130,246,.16), transparent 28%),
                radial-gradient(circle at 100% 0%, rgba(15,118,110,.10), transparent 22%),
                linear-gradient(180deg, #03122b 0%, #04152e 46%, #031126 100%) !important;
            color: var(--text-main) !important;
        }

        .stApp,
        .main,
        [data-testid="stAppViewContainer"],
        [data-testid="stHeader"] {
            background-color: transparent !important;
            color: var(--text-main) !important;
        }

        [data-testid="stToolbar"],
        [data-testid="stDecoration"] {
            background: transparent !important;
        }

        .block-container {
            max-width: 1320px !important;
            margin: 0 auto;
            padding-top: 1.15rem;
            padding-left: 1.1rem;
            padding-right: 1.1rem;
            padding-bottom: 3rem;
        }

        section[data-testid="stSidebar"] {
            background: #09162F !important;
            border-right: 1px solid rgba(148, 163, 184, .15);
        }

        .hero-shell {
            background: linear-gradient(135deg, rgba(8,21,44,.88), rgba(5,16,35,.88));
            border: 1px solid rgba(148,163,184,.10);
            border-radius: 26px;
            padding: 24px 26px;
            box-shadow: 0 18px 55px rgba(0,0,0,.20);
            margin-bottom: 18px;
        }

        .hero {
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap: 28px;
        }

        .hero-left {
            display:flex;
            align-items:center;
            gap: 28px;
            min-width:0;
        }

        .mw-mark {
            display:flex;
            align-items:center;
            border-right: 1px solid rgba(148,163,184,.16);
            padding-right: 30px;
            min-width:0;
        }

        .mw-logo {
            width: 178px;
            max-width: 178px;
            height:auto;
            display:block;
            object-fit:contain;
        }

        .mw-logo-fallback {
            color:#F8FAFC;
            font-size:1.35rem;
            font-weight:900;
            white-space:nowrap;
        }

        .hero-title h1 {
            margin:0;
            line-height:.95;
            font-weight:900;
            color:#F8FAFC;
        }

        .title-line {
            display:flex;
            align-items:baseline;
            gap:26px;
            flex-wrap:wrap;
        }

        .title-main {
            font-size:46px;
            letter-spacing:-.045em;
            color:#F8FAFC;
        }

        .title-service {
            font-size:46px;
            letter-spacing:-.035em;
            color:#9EC5FF;
            font-style:italic;
        }

        .hero-title p {
            margin:12px 0 0;
            color:#A9C7FF;
            font-weight:700;
            font-size:1rem;
        }

        .hero-right {
            display:flex;
            align-items:center;
            gap:22px;
        }

        .hero-meta {
            display:grid;
            grid-template-columns:auto auto;
            gap:8px 20px;
            align-items:center;
        }

        .hero-meta .k {
            color:#9EC5FF;
            text-transform:uppercase;
            letter-spacing:.17em;
            font-size:.72rem;
            font-weight:800;
        }

        .hero-meta .v {
            color:#FFF;
            font-weight:800;
            font-size:.90rem;
        }

        .hero-logo {
            width:92px;
            height:92px;
            object-fit:contain;
            background:rgba(255,255,255,.025);
            border-radius:16px;
            padding:7px;
        }

        .section-title {
            color:#A9C7FF;
            letter-spacing:.22em;
            text-transform:uppercase;
            font-weight:900;
            font-size:.76rem;
            margin:18px 0 12px;
            padding-left:10px;
            border-left:3px solid rgba(141,183,255,.72);
            opacity:.96;
        }

        .soft-rule {
            height:1px;
            width:100%;
            margin:4px 0 18px;
            background:linear-gradient(90deg, rgba(141,183,255,.22), rgba(148,163,184,.08), transparent);
        }

        .kpi-grid {
            display:grid;
            grid-template-columns:repeat(5, minmax(0, 1fr));
            gap:14px;
            margin:10px 0 18px;
        }

        .kpi-card {
            min-height:108px;
            background:linear-gradient(135deg, rgba(30,41,59,.96), rgba(15,23,42,.96));
            border:1px solid rgba(148,163,184,.14);
            border-radius:20px;
            padding:16px 18px;
            box-shadow:0 12px 34px rgba(0,0,0,.14);
            display:flex;
            flex-direction:column;
            justify-content:center;
            text-align:left;
        }

        .kpi-label {
            color:#A9C7FF;
            text-transform:uppercase;
            letter-spacing:.15em;
            font-size:.66rem;
            font-weight:900;
            margin-bottom:10px;
        }

        .kpi-value {
            color:#FFF;
            font-size:1.55rem;
            line-height:1.05;
            font-weight:900;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
        }

        .kpi-sub {
            color:#94A3B8;
            font-size:.78rem;
            font-weight:700;
            margin-top:8px;
            min-height:18px;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
        }

        .kpi-sub.good {
            color:#22C55E;
        }

        .panel,
        .account-card {
            background:linear-gradient(135deg, rgba(30,41,59,.88), rgba(15,23,42,.90));
            border:1px solid rgba(148,163,184,.12);
            border-radius:22px;
            padding:18px;
            box-shadow:0 14px 42px rgba(0,0,0,.14);
            text-align:left;
        }

        .account-card {
            margin-bottom:14px;
        }

        .account-head {
            display:flex;
            justify-content:space-between;
            gap:18px;
            align-items:flex-start;
        }

        .account-left {
            display:flex;
            gap:14px;
            align-items:flex-start;
        }

        .avatar {
            width:50px;
            height:50px;
            border-radius:16px;
            display:flex;
            align-items:center;
            justify-content:center;
            background:linear-gradient(135deg,#8DB7FF,#CABFFD);
            color:#FFF;
            font-weight:900;
            font-size:1.08rem;
            flex:0 0 auto;
        }

        .name {
            color:#FFF;
            font-size:1.12rem;
            font-weight:800;
            margin-bottom:2px;
        }

        .muted {
            color:#94A3B8;
            font-size:.86rem;
        }

        .money {
            color:#FFF;
            font-size:1.35rem;
            font-weight:900;
            text-align:left;
        }

        .submoney {
            color:#9EC5FF;
            font-size:.80rem;
            font-weight:800;
            text-align:left;
            margin-top:4px;
        }

        .bar-bg {
            margin-top:14px;
            width:100%;
            height:8px;
            border-radius:999px;
            background:rgba(148,163,184,.16);
            overflow:hidden;
        }

        .bar-fill {
            height:100%;
            border-radius:999px;
            background:linear-gradient(90deg,#8DB7FF,#CABFFD,#6EE7B7,#F7C561);
        }

        div[data-testid="stPlotlyChart"] {
            background:linear-gradient(135deg, rgba(30,41,59,.74), rgba(15,23,42,.76));
            border:1px solid rgba(148,163,184,.11);
            border-radius:24px;
            padding:14px 14px 10px;
            box-shadow:0 14px 42px rgba(0,0,0,.14);
        }

        .badge,
        .tax-pill,
        .liquidity-pill,
        .iof-pill {
            display:inline-block;
            padding:5px 9px;
            border-radius:999px;
            font-size:.72rem;
            font-weight:900;
            border:1px solid rgba(255,255,255,.14);
            white-space:nowrap;
        }

        .ok,
        .liquidity-pill {
            background:rgba(16,185,129,.14);
            color:#6EE7B7;
        }

        .warn,
        .iof-pill {
            background:rgba(245,158,11,.16);
            color:#FCD34D;
        }

        .danger,
        .tax-pill {
            background:rgba(239,68,68,.14);
            color:#FCA5A5;
        }

        .info {
            background:rgba(96,165,250,.16);
            color:#BFDBFE;
        }

        .table-shell {
            background:linear-gradient(135deg, rgba(30,41,59,.88), rgba(15,23,42,.90));
            border:1px solid rgba(148,163,184,.12);
            border-radius:20px;
            overflow-x:auto;
            overflow-y:hidden;
            width:100%;
            max-width:100%;
            box-shadow:0 12px 34px rgba(0,0,0,.12);
            scrollbar-width:thin;
            scrollbar-color:rgba(142,183,255,.45) rgba(15,23,42,.35);
        }

        .table-shell::-webkit-scrollbar {
            height:8px;
        }

        .table-shell::-webkit-scrollbar-track {
            background:rgba(15,23,42,.35);
            border-radius:999px;
        }

        .table-shell::-webkit-scrollbar-thumb {
            background:rgba(142,183,255,.45);
            border-radius:999px;
        }

        table.pretty {
            width:100%;
            min-width:100%;
            border-collapse:collapse;
            table-layout:fixed;
        }

        .table-shell.wide table.pretty {
            min-width:100%;
        }

        table.pretty thead {
            background:rgba(255,255,255,.035);
        }

        table.pretty th {
            color:#9EC5FF !important;
            font-size:.62rem;
            letter-spacing:.06em;
            text-transform:uppercase;
            padding:8px 9px;
            text-align:left;
            border-bottom:1px solid rgba(148,163,184,.14);
            white-space:normal;
            line-height:1.25;
        }

        table.pretty td {
            color:#F8FAFC !important;
            padding:8px 9px;
            border-bottom:1px solid rgba(148,163,184,.08);
            vertical-align:middle;
            font-weight:650;
            line-height:1.32;
            text-align:left;
            font-size:.78rem;
            white-space:normal;
            word-break:normal;
        }

        table.pretty tbody tr:last-child td {
            border-bottom:none;
        }

        table.pretty td.num,
        table.pretty th.num,
        table.pretty td.center,
        table.pretty th.center {
            text-align:left;
            white-space:normal;
        }

        table.pretty td.wrap {
            white-space:normal;
        }

        .empty-state {
            color:#94A3B8;
            padding:22px;
        }

        .stTabs [data-baseweb="tab-list"] {
            gap:24px;
            border-bottom:1px solid rgba(148,163,184,.14);
        }

        .stTabs [data-baseweb="tab"] {
            color:#A5B4FC !important;
            font-weight:800;
            padding-left:0;
            padding-right:0;
            background:transparent !important;
        }

        .stTabs [aria-selected="true"] {
            color:#FFF !important;
            border-bottom:3px solid #8DB7FF;
        }

        div[role="radiogroup"] {
            display:flex;
            flex-wrap:wrap;
            gap:8px;
            margin-bottom:14px;
        }

        div[role="radiogroup"] label {
            background:rgba(15,23,42,.78) !important;
            border:1px solid rgba(148,163,184,.18) !important;
            border-radius:999px !important;
            padding:7px 14px !important;
            color:#F8FAFC !important;
            font-weight:800 !important;
        }

        div[role="radiogroup"] label:hover {
            border-color:rgba(141,183,255,.55) !important;
            background:rgba(30,41,59,.92) !important;
        }

        div[role="radiogroup"] input {
            display:none !important;
        }

        div[role="radiogroup"] label * {
            color:#F8FAFC !important;
            background:transparent !important;
        }

        .stDownloadButton > button,
        .stButton > button {
            border-radius:999px !important;
            border:1px solid rgba(148,163,184,.22) !important;
            background:#111D33 !important;
            color:#F8FAFC !important;
            font-weight:800 !important;
            padding:.35rem .85rem !important;
            min-height:2.1rem !important;
            box-shadow:none !important;
        }

        .stDownloadButton > button:hover,
        .stButton > button:hover {
            background:#17243D !important;
            border-color:rgba(141,183,255,.55) !important;
            color:#FFFFFF !important;
        }

        input,
        textarea,
        [data-baseweb="input"] input,
        [data-baseweb="select"] {
            color:#F8FAFC !important;
            background-color:#111D33 !important;
            border-color:rgba(148,163,184,.22) !important;
        }

        label,
        p,
        span,
        div,
        button {
            color-scheme:dark !important;
        }

        .footer {
            text-align:center;
            color:#64748B;
            font-size:.78rem;
            margin-top:34px;
        }

        @media (max-width:1180px) {
            .kpi-grid {
                grid-template-columns:repeat(2, minmax(0,1fr));
            }
        }

        @media (max-width:1100px) {
            .hero {
                flex-direction:column;
                align-items:flex-start;
            }

            .hero-right {
                width:100%;
                justify-content:space-between;
            }

            .title-main,
            .title-service {
                font-size:38px;
            }

            .mw-logo {
                width:150px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(reference_date: str):
    botuvera_logo = logo_base64(BOTUVERA_LOGO)
    mwealth_logo = logo_base64(MWEALTH_LOGO)

    botuvera_logo_html = (
        f'<img class="hero-logo" src="data:image/png;base64,{botuvera_logo}" />'
        if botuvera_logo
        else ""
    )

    mwealth_logo_html = (
        f'<img class="mw-logo" src="data:image/png;base64,{mwealth_logo}" alt="M Wealth" />'
        if mwealth_logo
        else '<div class="mw-logo-fallback">M Wealth</div>'
    )

    st.markdown(
        f"""
        <div class="hero-shell">
            <div class="hero">
                <div class="hero-left">
                    <div class="mw-mark">{mwealth_logo_html}</div>
                    <div class="hero-title">
                        <h1 class="title-line">
                            <span class="title-main">Tesouraria</span>
                            <span class="title-service">As a Service</span>
                        </h1>
                        <p>{SUBTITLE}</p>
                    </div>
                </div>
                <div class="hero-right">
                    <div class="hero-meta">
                        <div class="k">Data de Atualização</div><div class="v">{reference_date}</div>
                        <div class="k">Parceiro</div><div class="v">{PARTNER}</div>
                        <div class="k">Gestor</div><div class="v">{GESTOR}</div>
                    </div>
                    {botuvera_logo_html}
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section(title: str):
    st.markdown(
        f'<div class="section-title">{html.escape(title)}</div><div class="soft-rule"></div>',
        unsafe_allow_html=True,
    )


def html_table(df: pd.DataFrame, col_labels=None, col_classes=None, allow_html_cols=None, wide=False):
    if df is None or df.empty:
        return '<div class="table-shell"><div class="empty-state">Sem dados para exibir.</div></div>'

    work = df.copy()
    columns = list(work.columns)
    labels = col_labels or {c: c for c in columns}
    classes = col_classes or {}
    allow_html_cols = set(allow_html_cols or [])
    shell_class = "table-shell wide" if wide else "table-shell"

    head = "".join(
        f'<th class="{classes.get(c, "")}">{html.escape(str(labels.get(c, c)))}</th>'
        for c in columns
    )

    rows = []
    for _, row in work.iterrows():
        tds = []
        for c in columns:
            val = row[c]
            txt = "—" if val is None or (isinstance(val, float) and pd.isna(val)) else str(val)
            txt = txt if c in allow_html_cols else html.escape(txt)
            tds.append(f'<td class="{classes.get(c, "")}">{txt}</td>')
        rows.append("<tr>" + "".join(tds) + "</tr>")

    return (
        f'<div class="{shell_class}">'
        '<table class="pretty">'
        f'<thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
        '</div>'
    )


def status_badge(ok: bool, text_ok="OK", text_bad="Atenção"):
    return f'<span class="badge {"ok" if ok else "danger"}">{text_ok if ok else text_bad}</span>'


def iof_badge(rate: int):
    cls = "ok" if int(rate) == 0 else "warn"
    return f'<span class="badge {cls}">{int(rate)}%</span>'


def kpi_card(label, value, sub="", cls=""):
    return (
        '<div class="kpi-card">'
        f'<div class="kpi-label">{html.escape(str(label))}</div>'
        f'<div class="kpi-value" title="{html.escape(str(value))}">{html.escape(str(value))}</div>'
        f'<div class="kpi-sub {html.escape(str(cls or ""))}">{html.escape(str(sub or ""))}</div>'
        '</div>'
    )


def render_kpis(items):
    st.markdown(
        '<div class="kpi-grid">' + ''.join(kpi_card(*item) for item in items) + '</div>',
        unsafe_allow_html=True,
    )


def load_clients():
    default = pd.DataFrame(
        [
            {"conta": "7983962", "titular": "Irineu Afonso", "tipo": "principal"},
            {"conta": "5166121", "titular": "Adriano Bissoni", "tipo": "principal"},
            {"conta": "4163084", "titular": "Vicente Bissoni", "tipo": "residual"},
            {"conta": "11370136", "titular": "Deise Cristina", "tipo": "residual"},
            {"conta": "9445242", "titular": "Transportes Botuverá", "tipo": "residual"},
        ]
    )

    if CLIENT_CONFIG.exists():
        try:
            cfg = pd.read_csv(CLIENT_CONFIG, dtype={"conta": str})
            if {"conta", "titular"}.issubset(cfg.columns):
                return cfg
        except Exception:
            pass

    return default



def is_variable_income_block(text: str) -> bool:
    """Identifica blocos de renda variável no relatório XP.

    Ex.: Fundos Imobiliários, Ações, ETFs, BDRs e Renda Variável.
    Esses blocos têm layout diferente de fundos/renda fixa.

    Importante: usa termos inteiros para não confundir "inflação"
    com "ação".
    """
    s = normalize_text(text)
    if any(term in s for term in ["fundos imobiliarios", "fundo imobiliario", "renda variavel", "bovespa"]):
        return True

    return bool(re.search(r"\b(fiis|fii|acoes|acao|bdrs|bdr|etfs|etf)\b", s))


def classify_variable_income_product(group_name: str, asset_name: str):
    s = normalize_text(f"{group_name or ''} {asset_name or ''}")
    if "fundo imobiliario" in s or "fundos imobiliarios" in s or "fii" in s:
        return "Fundos Imobiliários", "D+2", "risco"
    if "bdr" in s:
        return "BDRs", "D+2", "risco"
    if "etf" in s:
        return "ETFs", "D+2", "risco"
    return "Renda Variável", "D+2", "risco"


def build_variable_income_position_from_row(row, group_name: str, subgroup_name: str, account: str, titular: str):
    """Lê linhas de RV/FIIs, cujo layout XP costuma ser:
    Ativo | Qtd. Disponível | ... | Última Cotação | Posição

    A posição financeira costuma vir na última coluna.
    """
    asset = str(row.iloc[0]).strip() if len(row) > 0 and not is_empty(row.iloc[0]) else ""
    if not asset or normalize_text(asset) in ["ativo", "nan", "none"]:
        return None

    # Prioridade: última coluna preenchida com valor financeiro. No bloco de FIIs,
    # é a coluna "Posição". Fallback: maior valor monetário da linha.
    valor_bruto = parse_money(row.iloc[-1]) if len(row) > 1 else 0.0
    if valor_bruto <= 0:
        vals = []
        for item in list(row.iloc[1:]):
            v = parse_money(item)
            if v > 0:
                vals.append(v)
        vals_financeiros = [v for v in vals if v >= 100]
        if vals_financeiros:
            valor_bruto = max(vals_financeiros)

    if valor_bruto <= 0:
        return None

    produto, liquidez, fator = classify_variable_income_product(group_name, asset)

    return {
        "conta": str(account),
        "titular": titular,
        "ativo": asset.upper(),
        "produto": produto,
        "liquidez": liquidez,
        "fator": fator,
        "aplicacao": pd.NaT,
        "vencimento": pd.NaT,
        "dias_desde_aplicacao": None,
        "valor": valor_bruto,
        "valor_bruto": valor_bruto,
        "valor_liquido": valor_bruto,
        "ir": 0.0,
        "grupo_origem": group_name,
        "subgrupo_origem": subgroup_name,
    }


def classify_product(group_name: str, subgroup_name: str, asset_name: str):
    s = f"{group_name or ''} {subgroup_name or ''} {asset_name or ''}".lower()

    if "compromiss" in s:
        return "Op. Compromissadas", "D+0", "pos_fixado"

    if "saldo" in s:
        return "Saldo em Conta", "D+0", "caixa"

    if is_variable_income_block(s):
        return classify_variable_income_product(f"{group_name or ''} {subgroup_name or ''}", asset_name)

    if "fundo" in s or "fic" in s or "firf" in s or "fidc" in s:
        liq = map_fund_liquidity(asset_name, f"{group_name or ''} {subgroup_name or ''}")
        return fund_product_from_liquidity(liq), liq, "pos_fixado"

    if any(x in s for x in ["lca", "lci"]):
        # Renda fixa isenta não é D+0 por definição.
        # No detalhamento, o prazo correto vem do vencimento do papel.
        return "Renda Fixa Isenta", "Vencimento", "isento"

    if any(x in s for x in ["cdb", "tesouro", "letra financeira", "debenture", "debênture", "cra", "cri", "lf ", " lc "]):
        # Não travar renda fixa em D+31.
        # A liquidez operacional é o próprio vencimento do título.
        return "Renda Fixa", "Vencimento", "pos_fixado"

    return "Outros", "N/A", "outros"


def build_position_from_row(row, group_name: str, subgroup_name: str, account: str, titular: str, ref_date: date):
    group_text = f"{group_name or ''} {subgroup_name or ''}".lower()

    asset = ""
    appl = pd.NaT
    venc = pd.NaT
    valor_bruto = 0.0
    valor_liquido = 0.0

    if is_variable_income_block(group_text):
        return build_variable_income_position_from_row(row, group_name, subgroup_name, account, titular)

    if "fundo" in group_text:
        asset = str(row.iloc[0]).strip() if not is_empty(row.iloc[0]) else str(group_name).strip().upper()

        # Layout XP para fundos:
        # col 4 = Em cotização
        # col 5 = Posição
        # col 6 = Valor líquido
        em_cotizacao = parse_money(row.iloc[4]) if len(row) > 4 else 0.0
        posicao = parse_money(row.iloc[5]) if len(row) > 5 else 0.0
        liquido = parse_money(row.iloc[6]) if len(row) > 6 else 0.0

        # Posição total de tesouraria = posição atual + valores em cotização.
        valor_bruto = posicao + em_cotizacao
        valor_liquido = (liquido if liquido > 0 else posicao) + em_cotizacao

        if valor_bruto <= 0 and valor_liquido <= 0:
            vals = []
            for item in list(row.iloc[1:]):
                v = parse_money(item)
                if v > 0:
                    vals.append(v)

            vals_financeiros = [v for v in vals if v >= 100]
            if vals_financeiros:
                valor_bruto = max(vals_financeiros)
                valor_liquido = valor_bruto

    elif "compromiss" in group_text:
        asset = "OPERAÇÕES COMPROMISSADAS"
        appl = parse_date_br(row.iloc[1]) if len(row) > 1 else pd.NaT
        venc = parse_date_br(row.iloc[3]) if len(row) > 3 else pd.NaT
        valor_bruto = parse_money(row.iloc[8]) if len(row) > 8 else 0.0
        valor_liquido = parse_money(row.iloc[9]) if len(row) > 9 else 0.0

    else:
        asset = str(row.iloc[0]).strip() if not is_empty(row.iloc[0]) else str(group_name).strip().upper()
        appl = parse_date_br(row.iloc[1]) if len(row) > 1 else pd.NaT
        venc = parse_date_br(row.iloc[3]) if len(row) > 3 else pd.NaT
        valor_bruto = parse_money(row.iloc[8]) if len(row) > 8 else 0.0
        valor_liquido = parse_money(row.iloc[9]) if len(row) > 9 else 0.0

    if valor_bruto <= 0 and valor_liquido <= 0:
        return None

    if valor_bruto <= 0:
        valor_bruto = valor_liquido

    if valor_liquido <= 0:
        valor_liquido = valor_bruto

    produto, liquidez, fator = classify_product(group_name, subgroup_name, asset)

    if "fundo" in group_text or "cotiza" in group_text or "cotiz" in group_text or "fidc" in group_text:
        liquidez = map_fund_liquidity(asset, group_text)
        produto, liquidez, fator = fund_product_from_liquidity(liquidez), liquidez, "pos_fixado"

    days = None
    if isinstance(appl, date) and not pd.isna(appl) and isinstance(ref_date, date):
        try:
            days = max((ref_date - appl).days, 0)
        except Exception:
            days = None

    return {
        "conta": str(account),
        "titular": titular,
        "ativo": asset.upper(),
        "produto": produto,
        "liquidez": liquidez,
        "fator": fator,
        "aplicacao": appl,
        "vencimento": venc,
        "dias_desde_aplicacao": days,
        "valor": valor_bruto,
        "valor_bruto": valor_bruto,
        "valor_liquido": valor_liquido,
        "ir": max(valor_bruto - valor_liquido, 0.0),
        "grupo_origem": group_name,
        "subgrupo_origem": subgroup_name,
    }


def parse_xp_file(file_obj, filename: str, clients: pd.DataFrame):
    df = pd.read_excel(file_obj, sheet_name=0, header=None, dtype=object, engine="openpyxl")

    header_text = " ".join([str(x) for x in df.iloc[0].dropna().tolist()])
    account_match = re.search(r"Conta:\s*(\d+)", header_text)
    account = account_match.group(1) if account_match else re.sub(r"\D+", "", filename)

    date_match = re.search(r"(\d{2}/\d{2}/\d{4})", header_text)
    ref_date = datetime.strptime(date_match.group(1), "%d/%m/%Y").date() if date_match else date.today()

    client_match = clients[clients["conta"].astype(str) == str(account)]
    titular = client_match.iloc[0]["titular"] if not client_match.empty else f"Conta {account}"
    tipo = client_match.iloc[0]["tipo"] if (not client_match.empty and "tipo" in client_match.columns) else ""

    patrimonio = parse_money(df.iloc[3, 0]) if df.shape[0] > 3 else 0.0
    saldo_disponivel = parse_money(df.iloc[3, 2]) if df.shape[0] > 3 and df.shape[1] > 2 else 0.0

    positions = []
    current_group = None
    current_subgroup = None
    capture_rows = False

    for _, row in df.iterrows():
        first = "" if is_empty(row.iloc[0]) else str(row.iloc[0]).strip()
        values_lower = [str(x).strip().lower() for x in row.tolist() if not is_empty(x)]

        if first.startswith("Saldo Disponível"):
            capture_rows = False
            current_subgroup = None
            continue

        if first and "|" in first and re.match(r"^\s*\d+[\d,.]*%\|", first):
            label = first.split("|", 1)[1].strip()
            is_header_row = any(v in ["aplicação", "data cota"] for v in values_lower)

            if is_header_row:
                current_subgroup = label
                capture_rows = True
            else:
                current_group = label
                current_subgroup = None
                capture_rows = False

            continue

        # Blocos de renda variável/FIIs da XP têm cabeçalho sem percentual,
        # normalmente iniciando com "Ativo". Ex.: Fundos Imobiliários.
        if current_group and is_variable_income_block(current_group) and normalize_text(first) == "ativo":
            current_subgroup = current_group
            capture_rows = True
            continue

        if capture_rows:
            if all(is_empty(x) for x in row.tolist()):
                capture_rows = False
                continue

            pos = build_position_from_row(row, current_group, current_subgroup, account, titular, ref_date)
            if pos is not None:
                positions.append(pos)

    if saldo_disponivel > 0:
        positions.append(
            {
                "conta": str(account),
                "titular": titular,
                "ativo": "SALDO EM CONTA",
                "produto": "Saldo em Conta",
                "liquidez": "D+0",
                "fator": "caixa",
                "aplicacao": pd.NaT,
                "vencimento": pd.NaT,
                "dias_desde_aplicacao": None,
                "valor": saldo_disponivel,
                "valor_bruto": saldo_disponivel,
                "valor_liquido": saldo_disponivel,
                "ir": 0.0,
                "grupo_origem": "Saldo em Conta",
                "subgrupo_origem": "Saldo em Conta",
            }
        )

    summary = {
        "conta": str(account),
        "titular": titular,
        "tipo": tipo,
        "patrimonio_arquivo": patrimonio,
        "saldo_disponivel": saldo_disponivel,
        "data_referencia": ref_date,
        "arquivo": filename,
    }

    return pd.DataFrame(positions), summary


def get_mtime_token():
    files = list(DEFAULT_POSITIONS_DIR.glob("*.xlsx"))
    return sum(f.stat().st_mtime for f in files) if files else 0.0


@st.cache_data(show_spinner=False)
def load_data_from_disk(_mtime_token: float):
    clients = load_clients()
    files = sorted(DEFAULT_POSITIONS_DIR.glob("*.xlsx"))
    parsed = []

    for file in files:
        pos, summ = parse_xp_file(file, file.name, clients)
        parsed.append((pos, summ))

    if not parsed:
        return pd.DataFrame(), pd.DataFrame()

    summaries_all = pd.DataFrame([s for _, s in parsed])
    if summaries_all.empty:
        return pd.DataFrame(), summaries_all

    summaries_all["data_referencia_dt"] = pd.to_datetime(summaries_all["data_referencia"], errors="coerce")
    latest = summaries_all.sort_values(["conta", "data_referencia_dt"]).groupby("conta", as_index=False).tail(1)
    keep_files = set(latest["arquivo"].tolist())

    all_positions = []
    summaries = []

    for pos, summ in parsed:
        if summ["arquivo"] in keep_files:
            if not pos.empty:
                all_positions.append(pos)
            summaries.append(summ)

    positions = pd.concat(all_positions, ignore_index=True) if all_positions else pd.DataFrame()
    summary = pd.DataFrame(summaries)

    return positions, summary


def load_data_from_uploads(uploaded_files):
    clients = load_clients()
    all_positions = []
    summaries = []

    for up in uploaded_files:
        pos, summ = parse_xp_file(up, up.name, clients)
        if not pos.empty:
            all_positions.append(pos)
        summaries.append(summ)

    positions = pd.concat(all_positions, ignore_index=True) if all_positions else pd.DataFrame()
    summary = pd.DataFrame(summaries)

    return positions, summary



def load_fund_applications():
    path = FUND_APPLICATIONS_FILE if FUND_APPLICATIONS_FILE.exists() else FUND_APPLICATIONS_FALLBACK

    if not path.exists():
        return pd.DataFrame()

    try:
        df = pd.read_excel(path, dtype=str)
    except Exception:
        return pd.DataFrame()

    colmap = {normalize_text(c): c for c in df.columns}

    conta_col = colmap.get("conta")
    fundo_col = colmap.get("fundo")
    data_col = (
        colmap.get("data")
        or colmap.get("data de aplicacao")
        or colmap.get("data aplicacao")
    )
    tipo_col = colmap.get("tipo")
    valor_col = (
        colmap.get("valor")
        or colmap.get("valor aplicacao")
        or colmap.get("valor de aplicacao")
        or colmap.get("valor aplicado")
    )

    if not all([conta_col, fundo_col, data_col, valor_col]):
        return pd.DataFrame()

    cols = [conta_col, fundo_col, data_col, valor_col]
    if tipo_col:
        cols.append(tipo_col)

    out = df[cols].copy()
    rename = {
        conta_col: "conta",
        fundo_col: "fundo",
        data_col: "data_movimento",
        valor_col: "valor_movimento",
    }
    if tipo_col:
        rename[tipo_col] = "tipo"

    out = out.rename(columns=rename)

    if "tipo" not in out.columns:
        out["tipo"] = "Aplicação"

    out["conta"] = out["conta"].astype(str).str.replace(r"\D", "", regex=True)
    out["fundo"] = out["fundo"].astype(str).str.strip()
    out["fundo_norm"] = out["fundo"].apply(normalize_text)
    out["data_movimento"] = out["data_movimento"].apply(parse_fund_movement_date)
    out["valor_movimento"] = out["valor_movimento"].apply(parse_money)
    out["tipo_norm"] = out["tipo"].apply(normalize_text)

    out.loc[out["tipo_norm"].str.contains("resgate|retirada|saque", na=False), "tipo_norm"] = "resgate"
    out.loc[~out["tipo_norm"].eq("resgate"), "tipo_norm"] = "aplicacao"

    out = out.dropna(subset=["data_movimento"])
    out = out[out["valor_movimento"] > 0]

    return out


def prepare_fund_positions_for_matching(positions: pd.DataFrame) -> pd.DataFrame:
    funds = positions[positions["produto"].str.contains("Fundos", case=False, na=False)].copy()

    if funds.empty:
        return pd.DataFrame()

    funds["fundo_norm"] = funds["ativo"].apply(normalize_text)
    funds["liquidez_mapeada"] = funds.apply(lambda r: map_fund_liquidity(r.get("ativo", ""), f"{r.get('grupo_origem', '')} {r.get('subgrupo_origem', '')}"), axis=1)
    funds["produto_mapeado"] = funds["liquidez_mapeada"].apply(fund_product_from_liquidity)

    # Soma posição normal + eventuais valores em cotização/resgate em trânsito
    # para chegar na posição total atual do fundo/produto.
    grouped = funds.groupby(["conta", "fundo_norm"], as_index=False).agg(
        ativo=("ativo", "first"),
        titular=("titular", "first"),
        produto=("produto_mapeado", "first"),
        liquidez=("liquidez_mapeada", "first"),
        valor_bruto=("valor_bruto", "sum"),
        valor_liquido=("valor_liquido", "sum"),
        ir=("ir", "sum"),
    )

    return grouped


def match_current_fund_position(funds: pd.DataFrame, conta: str, fundo_norm: str):
    same_account = funds[funds["conta"].astype(str) == str(conta)]

    if same_account.empty:
        return pd.Series(dtype=object)

    scored = same_account.copy()
    scored["score_match"] = scored["fundo_norm"].apply(lambda x: fund_match_score(fundo_norm, x))
    scored = scored.sort_values("score_match", ascending=False)

    if scored.empty or float(scored.iloc[0]["score_match"]) < 0.48:
        return pd.Series(dtype=object)

    return scored.iloc[0]


def assign_fund_movements_to_positions(apps: pd.DataFrame, funds: pd.DataFrame) -> pd.DataFrame:
    if apps.empty or funds.empty:
        return pd.DataFrame()

    assigned = []

    for _, mov in apps.iterrows():
        same_account = funds[funds["conta"].astype(str) == str(mov["conta"])]
        if same_account.empty:
            continue

        scored = same_account.copy()
        scored["score_match"] = scored["fundo_norm"].apply(lambda x: fund_match_score(mov["fundo_norm"], x))
        scored = scored.sort_values("score_match", ascending=False)

        if scored.empty or float(scored.iloc[0]["score_match"]) < 0.48:
            continue

        best = scored.iloc[0]
        item = mov.to_dict()
        item["current_fundo_norm"] = best["fundo_norm"]
        item["current_ativo"] = best["ativo"]
        item["score_match"] = float(best["score_match"])
        assigned.append(item)

    return pd.DataFrame(assigned)


def build_fund_lots(apps: pd.DataFrame, positions: pd.DataFrame):
    # A posição XP é a fonte oficial de valores financeiros.
    # A planilha de aplicações/resgates serve apenas para montar os lotes de datas,
    # calcular IOF e dividir a posição atual por lote quando houver mais de uma data.
    funds = prepare_fund_positions_for_matching(positions)

    if funds.empty:
        return pd.DataFrame()

    assigned = assign_fund_movements_to_positions(apps, funds) if not apps.empty else pd.DataFrame()
    lots = []

    for _, current in funds.iterrows():
        conta = str(current.get("conta", ""))
        current_norm = str(current.get("fundo_norm", ""))
        current_name = str(current.get("ativo", ""))

        valor_bruto_atual = float(current.get("valor_bruto", 0) or 0)
        valor_liquido_atual = float(current.get("valor_liquido", 0) or 0)
        ir_atual = float(current.get("ir", 0) or 0)

        if valor_bruto_atual <= 0:
            continue

        if assigned.empty:
            movs = pd.DataFrame()
        else:
            movs = assigned[
                (assigned["conta"].astype(str) == conta)
                & (assigned["current_fundo_norm"].astype(str) == current_norm)
            ].sort_values("data_movimento")

        lotes = []

        if not movs.empty:
            for _, mov in movs.iterrows():
                valor = float(mov["valor_movimento"] or 0)
                if valor <= 0:
                    continue

                if mov["tipo_norm"] == "resgate":
                    # O valor do resgate pode vir com rendimento embutido.
                    # Ainda assim, usamos apenas para estimar quais lotes antigos foram consumidos.
                    restante_resgate = valor

                    while restante_resgate > 0 and lotes:
                        abatimento = min(lotes[0]["saldo_lote"], restante_resgate)
                        lotes[0]["saldo_lote"] -= abatimento
                        restante_resgate -= abatimento

                        if lotes[0]["saldo_lote"] <= 0.01:
                            lotes.pop(0)
                else:
                    lotes.append(
                        {
                            "conta": conta,
                            "fundo": current_name,
                            "fundo_norm": current_norm,
                            "data_aplicacao": mov["data_movimento"],
                            "valor_aplicado_original": valor,
                            "saldo_lote": valor,
                            "liquidez": current.get("liquidez", "N/A"),
                            "produto": current.get("produto", "Fundos"),
                            "fonte_lote": "movimentacao",
                        }
                    )

            # Se os resgates zeraram os lotes pelo valor nominal, mas a XP ainda mostra posição,
            # preserva uma data de referência da própria planilha em vez de esconder o fundo.
            if not lotes and valor_bruto_atual > 0:
                datas_aplicacao = movs.loc[movs["tipo_norm"].eq("aplicacao"), "data_movimento"].dropna()
                data_base = datas_aplicacao.min() if not datas_aplicacao.empty else pd.NaT
                lotes.append(
                    {
                        "conta": conta,
                        "fundo": current_name,
                        "fundo_norm": current_norm,
                        "data_aplicacao": data_base,
                        "valor_aplicado_original": 0.0,
                        "saldo_lote": valor_bruto_atual,
                        "liquidez": current.get("liquidez", "N/A"),
                        "produto": current.get("produto", "Fundos"),
                        "fonte_lote": "residual_posicao_xp",
                    }
                )
        else:
            # Fundo existe na XP, mas não existe movimento compatível na planilha.
            lotes.append(
                {
                    "conta": conta,
                    "fundo": current_name,
                    "fundo_norm": current_norm,
                    "data_aplicacao": pd.NaT,
                    "valor_aplicado_original": 0.0,
                    "saldo_lote": valor_bruto_atual,
                    "liquidez": current.get("liquidez", "N/A"),
                    "produto": current.get("produto", "Fundos"),
                    "fonte_lote": "sem_movimentacao",
                }
            )

        saldo_total_lotes = sum(float(l.get("saldo_lote", 0) or 0) for l in lotes)
        if saldo_total_lotes <= 0:
            saldo_total_lotes = valor_bruto_atual

        for lote in lotes:
            saldo_lote = float(lote.get("saldo_lote", 0) or 0)
            if saldo_lote <= 0.01:
                continue

            peso_lote = safe_div(saldo_lote, saldo_total_lotes)

            lote["valor_bruto_atual"] = valor_bruto_atual * peso_lote
            lote["valor_liquido_atual"] = valor_liquido_atual * peso_lote
            lote["ir_atual"] = ir_atual * peso_lote
            lote["saldo_lote"] = valor_bruto_atual * peso_lote
            lots.append(lote)

    return pd.DataFrame(lots)


def enrich_fund_efficiency(positions: pd.DataFrame, reference_date: date) -> pd.DataFrame:
    apps = load_fund_applications()

    if positions.empty:
        return pd.DataFrame()

    lots = build_fund_lots(apps, positions)

    if lots.empty:
        return pd.DataFrame()

    rows = []

    for _, lote in lots.iterrows():
        data_aplicacao = lote["data_aplicacao"]

        if isinstance(data_aplicacao, date) and not pd.isna(data_aplicacao):
            dias = max((reference_date - data_aplicacao).days, 0)
            iof_rate = iof_rate_by_days(dias)
            data_zeragem = data_aplicacao + timedelta(days=30)
            dias_zerar = max((data_zeragem - reference_date).days, 0)
            status = "IOF zerado" if iof_rate == 0 else "Aguardando zeragem"
        else:
            dias = np.nan
            iof_rate = np.nan
            data_zeragem = pd.NaT
            dias_zerar = np.nan
            status = "Sem data de aplicação"

        rows.append(
            {
                "conta": lote["conta"],
                "fundo": lote["fundo"],
                "data_aplicacao": data_aplicacao,
                "valor_aplicado": float(lote["valor_aplicado_original"] or 0),
                "saldo_lote": float(lote["saldo_lote"] or 0),
                "dias_desde_aplicacao": dias,
                "aliquota_iof": iof_rate,
                "dias_ate_zerar": dias_zerar,
                "data_zeragem": data_zeragem,
                "liquidez": lote.get("liquidez", "N/A"),
                "produto": lote.get("produto", "Fundos"),
                "valor_bruto_atual": float(lote["valor_bruto_atual"] or 0),
                "valor_liquido_atual": float(lote["valor_liquido_atual"] or 0),
                "ir_atual": float(lote["ir_atual"] or 0),
                "status": status,
                "fonte_lote": lote.get("fonte_lote", ""),
            }
        )

    return pd.DataFrame(rows)


def enrich(positions: pd.DataFrame, summary: pd.DataFrame, reference_date: date):
    if positions.empty:
        return positions, summary

    positions = positions.copy()

    for col in ["valor", "valor_bruto", "valor_liquido", "ir"]:
        if col not in positions.columns:
            positions[col] = positions["valor"] if col in ["valor_bruto", "valor_liquido"] else 0.0
        positions[col] = pd.to_numeric(positions[col], errors="coerce").fillna(0.0)

    positions.loc[positions["valor_bruto"] <= 0, "valor_bruto"] = positions.loc[
        positions["valor_bruto"] <= 0, "valor_liquido"
    ]
    positions.loc[positions["valor_liquido"] <= 0, "valor_liquido"] = positions.loc[
        positions["valor_liquido"] <= 0, "valor_bruto"
    ]

    positions["valor"] = positions["valor_bruto"]
    positions["ir"] = (positions["valor_bruto"] - positions["valor_liquido"]).clip(lower=0).round(2)

    fund_mask = positions["produto"].astype(str).str.contains("Fundos", case=False, na=False)
    if fund_mask.any():
        mapped_liq = positions.loc[fund_mask].apply(lambda r: map_fund_liquidity(r.get("ativo", ""), f"{r.get('grupo_origem', '')} {r.get('subgrupo_origem', '')}"), axis=1)
        positions.loc[fund_mask, "liquidez"] = mapped_liq.values
        positions.loc[fund_mask, "produto"] = mapped_liq.apply(fund_product_from_liquidity).values

    positions["aplicacao_fmt"] = positions["aplicacao"].apply(fmt_date_br)
    positions["vencimento_fmt"] = positions["vencimento"].apply(fmt_date_br)
    positions["dias_ate_vencimento"] = positions["vencimento"].apply(lambda x: days_until_date(x, reference_date))

    totals_by_account = positions.groupby("conta")["valor"].sum().rename("patrimonio")
    liquid_by_account = positions.groupby("conta")["valor_liquido"].sum().rename("patrimonio_liquido")
    ir_by_account = positions.groupby("conta")["ir"].sum().rename("ir_total")

    positions = positions.merge(totals_by_account, on="conta", how="left", suffixes=("", "_conta"))
    positions["participacao_conta"] = positions["valor"] / positions["patrimonio"]
    positions["participacao_total"] = positions["valor"] / positions["valor"].sum()

    summary = summary.copy()

    if not summary.empty:
        summary["patrimonio"] = summary["conta"].astype(str).map(totals_by_account.to_dict()).fillna(
            summary.get("patrimonio_arquivo", 0)
        )
        summary["patrimonio_liquido"] = summary["conta"].astype(str).map(liquid_by_account.to_dict()).fillna(
            summary["patrimonio"]
        )
        summary["ir_total"] = summary["conta"].astype(str).map(ir_by_account.to_dict()).fillna(0.0)
        summary["participacao_total"] = summary["patrimonio"] / summary["patrimonio"].sum()
        summary["participacao_fmt"] = summary["participacao_total"].apply(pct)
        summary["iniciais"] = summary["titular"].apply(lambda s: "".join([p[0] for p in str(s).split()[:2]]).upper())
        summary["posicoes"] = summary["conta"].astype(str).map(positions.groupby("conta").size().to_dict()).fillna(0).astype(int)

    fund_eff = enrich_fund_efficiency(positions, reference_date)

    if not fund_eff.empty:
        eff_cols = fund_eff[
            [
                "conta",
                "fundo",
                "data_aplicacao",
                "dias_desde_aplicacao",
                "aliquota_iof",
                "dias_ate_zerar",
                "data_zeragem",
            ]
        ].copy()

        eff_cols["fundo_norm"] = eff_cols["fundo"].apply(normalize_text)
        positions["ativo_norm"] = positions["ativo"].apply(normalize_text)

        for _, r in eff_cols.iterrows():
            mask = (
                (positions["conta"].astype(str) == str(r["conta"]))
                & (
                    positions["ativo_norm"].apply(
                        lambda x: r["fundo_norm"] in x or x in r["fundo_norm"]
                    )
                )
            )

            positions.loc[mask, "aplicacao"] = r["data_aplicacao"]
            positions.loc[mask, "aplicacao_fmt"] = fmt_date_br(r["data_aplicacao"])
            positions.loc[mask, "dias_desde_aplicacao"] = r["dias_desde_aplicacao"]
            positions.loc[mask, "iof_fundo"] = r["aliquota_iof"]
            positions.loc[mask, "dias_ate_zerar_iof"] = r["dias_ate_zerar"]
            positions.loc[mask, "data_zeragem_iof"] = r["data_zeragem"]

        positions = positions.drop(columns=["ativo_norm"], errors="ignore")

    return positions, summary


def calc_kpis(positions: pd.DataFrame, summary: pd.DataFrame):
    total = float(positions["valor"].sum()) if not positions.empty else 0.0
    total_liquido = float(positions["valor_liquido"].sum()) if "valor_liquido" in positions.columns else total
    ir_total = float(positions["ir"].sum()) if "ir" in positions.columns else max(total - total_liquido, 0)
    liq_d0 = float(positions[positions["liquidez"].isin(["D+0", "D+1"])]["valor"].sum())
    isenta = float(positions[positions["produto"].eq("Renda Fixa Isenta")]["valor"].sum())
    travado = float(positions[~positions["liquidez"].isin(["D+0", "D+1"])]["valor"].sum())
    maior = summary.sort_values("patrimonio", ascending=False).iloc[0] if not summary.empty else None

    return {
        "total": total,
        "total_liquido": total_liquido,
        "ir_total": ir_total,
        "liquidez_d0": liq_d0,
        "liquidez_d0_pct": safe_div(liq_d0, total),
        "isenta": isenta,
        "isenta_pct": safe_div(isenta, total),
        "travado": travado,
        "travado_pct": safe_div(travado, total),
        "contas": int(summary["conta"].nunique()) if not summary.empty else 0,
        "maior_titular_nome": str(maior["titular"]) if maior is not None else "—",
        "maior_titular_pct": float(maior["participacao_total"]) if maior is not None else 0.0,
    }


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Detalhamento")
    return output.getvalue()


def render_account_card(row, total_geral: float):
    participacao = pct(safe_div(row["patrimonio"], total_geral))

    st.markdown(
        f"""
        <div class="account-card" style="padding:16px 18px;">
            <div class="account-head" style="align-items:center;">
                <div class="account-left" style="align-items:center;">
                    <div class="avatar">{html.escape(str(row['iniciais']))}</div>
                    <div>
                        <div class="name" style="margin-bottom:0;">{html.escape(str(row['titular']))}</div>
                    </div>
                </div>
                <div style="min-width:220px; text-align:left;">
                    <div class="money" style="font-size:1.15rem; margin-bottom:2px;">{brl(row['patrimonio'])}</div>
                    <div class="submoney" style="margin-top:0;">{participacao} do total</div>
                </div>
            </div>
            <div class="bar-bg" style="margin-top:12px;">
                <div class="bar-fill" style="width:{max(min(100 * safe_div(row['patrimonio'], total_geral), 100), 0):.2f}%"></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )




def sort_product_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    work = df.copy()
    work["_produto_ordem"] = work["produto"].apply(product_sort_key) if "produto" in work.columns else 9
    work["_liquidez_ordem"] = work["liquidez"].apply(liquidity_to_days) if "liquidez" in work.columns else 99999
    return work.sort_values(["_produto_ordem", "_liquidez_ordem", "produto"], ascending=[True, True, True]).drop(columns=["_produto_ordem", "_liquidez_ordem"], errors="ignore")


def render_account_strategy_expander(row, positions):
    conta = str(row["conta"])
    titular = str(row["titular"])

    detail = positions[positions["conta"].astype(str) == conta].copy()

    if detail.empty:
        return

    produto = detail.groupby("produto", as_index=False).agg(
        valor=("valor", "sum"),
        valor_liquido=("valor_liquido", "sum"),
        ir=("ir", "sum"),
        liquidez=(
            "liquidez",
            lambda s: ", ".join(
                sorted(
                    s.unique(),
                    key=lambda x: LIQUIDITY_ORDER.index(x) if x in LIQUIDITY_ORDER else 99,
                )
            ),
        ),
    )
    produto = sort_product_summary(produto)

    produto["participacao"] = produto["valor"] / produto["valor"].sum()

    produto["Part."] = produto["participacao"].apply(pct)
    produto["Bruto"] = produto["valor"].apply(brl)
    produto["IR"] = produto["ir"].apply(brl)
    produto["Líquido"] = produto["valor_liquido"].apply(brl)

    produto = produto[["produto", "liquidez", "Part.", "Bruto", "IR", "Líquido"]]
    produto.columns = ["Produto", "Liq.", "Part.", "Bruto", "IR", "Líquido"]

    with st.expander(f"Ver consolidado por estratégia — {titular}", expanded=False):
        st.markdown(html_table(produto, wide=False), unsafe_allow_html=True)
def render_visao_geral(positions, summary, kpis):
    render_kpis(
        [
            ("Patrimônio bruto", short_money(kpis["total"]), "Valor de posição", ""),
            ("Valor líquido", short_money(kpis["total_liquido"]), "Após IR estimado", ""),
            ("IR estimado", brl(kpis["ir_total"]), "Bruto - líquido", ""),
            ("Liquidez D+0/D+1", pct(kpis["liquidez_d0_pct"]), brl(kpis["liquidez_d0"]), "good"),
            ("Maior titular", pct(kpis["maior_titular_pct"]), kpis["maior_titular_nome"], "good"),
        ]
    )

    section("Distribuição por produto")

    left, right = st.columns([0.78, 1.22], vertical_alignment="top")

    prod = positions.groupby("produto", as_index=False).agg(
        valor=("valor", "sum"),
        valor_liquido=("valor_liquido", "sum"),
        ir=("ir", "sum"),
        liquidez=(
            "liquidez",
            lambda s: ", ".join(
                sorted(
                    s.unique(),
                    key=lambda x: LIQUIDITY_ORDER.index(x) if x in LIQUIDITY_ORDER else 99,
                )
            ),
        ),
    )
    prod = sort_product_summary(prod)

    prod["participacao"] = prod["valor"] / prod["valor"].sum()

    with left:
        fig = go.Figure(
            go.Pie(
                labels=prod["produto"],
                values=prod["valor"],
                hole=0.62,
                textinfo="none",
                marker=dict(colors=["#8DB7FF", "#6EE7B7", "#CABFFD", "#F7C561", "#94A3B8"]),
            )
        )

        fig.update_layout(
            height=255,
            margin=dict(l=0, r=0, t=0, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#E2E8F0"),
            showlegend=False,
            annotations=[
                dict(
                    text=short_money(kpis["total"]),
                    showarrow=False,
                    font=dict(size=16, color="#FFF", family="Inter"),
                )
            ],
        )

        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    with right:
        disp = prod.copy()
        disp["Part."] = disp["participacao"].apply(pct)
        disp["Bruto"] = disp["valor"].apply(brl)
        disp["IR"] = disp["ir"].apply(brl)
        disp["Líquido"] = disp["valor_liquido"].apply(brl)
        disp = disp[["produto", "liquidez", "Part.", "Bruto", "IR", "Líquido"]]
        disp.columns = ["Produto", "Liq.", "Part.", "Bruto", "IR", "Líquido"]

        st.markdown(html_table(disp, wide=False), unsafe_allow_html=True)

    section("Posição por titular")

    for _, row in summary.sort_values("patrimonio", ascending=False).iterrows():
        render_account_card(row, kpis["total"])
        render_account_strategy_expander(row, positions)


def render_eficiencia_fundos(positions, reference_date: date):
    section("Eficiência dos fundos")

    eff = enrich_fund_efficiency(positions, reference_date)

    if eff.empty:
        st.markdown(
            '<div class="panel"><div class="muted">Sem planilha de movimentações de fundos encontrada. Use <b>data/config/aplicacoes_fundos.xlsx</b> com as colunas Conta, Fundo, Data, Tipo e Valor.</div></div>',
            unsafe_allow_html=True,
        )
        return

    view = eff.copy()
    view["_liq_ordem"] = view["liquidez"].apply(liquidity_to_days) if "liquidez" in view.columns else 99999
    view["_data_ordem"] = pd.to_datetime(view["data_aplicacao"], errors="coerce").fillna(pd.Timestamp("2262-04-11"))
    view = view.sort_values(["conta", "_liq_ordem", "_data_ordem", "fundo"])
    view["Liq."] = view["liquidez"].apply(lambda x: f'<span class="liquidity-pill">{html.escape(str(x))}</span>') if "liquidez" in view.columns else "—"
    view["Aplicação"] = view["data_aplicacao"].apply(fmt_date_br)
    view["Dias"] = view["dias_desde_aplicacao"].apply(lambda x: "—" if pd.isna(x) else f"{int(x)}d")
    view["IOF"] = view["aliquota_iof"].apply(lambda x: "—" if pd.isna(x) else iof_badge(int(x)))
    view["Zera em"] = view["dias_ate_zerar"].apply(lambda x: "—" if pd.isna(x) else ("zerado" if int(x) == 0 else f"{int(x)}d"))
    view["Data zero"] = view["data_zeragem"].apply(fmt_date_br)
    view["Bruto XP"] = view["valor_bruto_atual"].apply(brl)
    view["Líquido XP"] = view["valor_liquido_atual"].apply(brl)
    view["IR XP"] = view["ir_atual"].apply(brl)
    view["Status"] = view["status"].apply(lambda s: status_badge(s == "IOF zerado", "Zerado", "Aguard." if s != "Sem data de aplicação" else "Sem data"))

    out = view[
        [
            "conta",
            "fundo",
            "Liq.",
            "Aplicação",
            "Dias",
            "IOF",
            "Zera em",
            "Data zero",
            "Bruto XP",
            "Líquido XP",
            "IR XP",
            "Status",
        ]
    ].copy()

    out.columns = [
        "Conta",
        "Fundo",
        "Liq.",
        "Aplicação",
        "Dias",
        "IOF",
        "Zera em",
        "Data zero",
        "Bruto XP",
        "Líquido XP",
        "IR XP",
        "Status",
    ]

    st.markdown(
        html_table(out, allow_html_cols=["Liq.", "IOF", "Status"], wide=False),
        unsafe_allow_html=True,
    )


def render_eficiencia_compromissadas(df):
    section("Eficiência das compromissadas")

    comp = df[df["produto"].eq("Op. Compromissadas")].copy()
    comp["_venc_ordem"] = pd.to_datetime(comp["vencimento"], errors="coerce").fillna(pd.Timestamp("2262-04-11"))
    comp = comp.sort_values(["_venc_ordem", "conta", "valor_bruto"], ascending=[True, True, False])

    if comp.empty:
        st.markdown(
            '<div class="panel"><div class="muted">Não há operações compromissadas no filtro selecionado.</div></div>',
            unsafe_allow_html=True,
        )
        return

    rows = []
    for _, r in comp.iterrows():
        rows.append(
            {
                "Titular": r["titular"],
                "Conta": r["conta"],
                "Aplicação": r["aplicacao_fmt"],
                "Vencimento": r["vencimento_fmt"],
                "Dias": "—" if pd.isna(r["dias_desde_aplicacao"]) else f"{int(r['dias_desde_aplicacao'])}d",
                "Bruto": brl(r["valor_bruto"]),
                "Líquido": brl(r["valor_liquido"]),
                "IR": brl(r["ir"]),
            }
        )

    table = pd.DataFrame(rows)
    st.markdown(html_table(table, wide=False), unsafe_allow_html=True)




def product_sort_key(produto: str) -> int:
    p = normalize_text(produto)
    if "compromiss" in p:
        return 0
    if "renda fixa" in p:
        return 1
    if "fundo" in p:
        return 2
    if any(x in p for x in ["renda variavel", "fundos imobiliarios", "bdr", "etf"]):
        return 3
    if "saldo" in p or "caixa" in p:
        return 4
    return 9


def sort_positions_for_cashflow(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    work["_produto_ordem"] = work["produto"].apply(product_sort_key)
    work["_liquidez_ordem"] = work["liquidez"].apply(liquidity_to_days)

    # Para renda fixa e compromissadas, a cascata correta é pelo vencimento
    # mais próximo em relação à data de atualização da posição.
    work["_vencimento_ordem"] = pd.to_datetime(work["vencimento"], errors="coerce")
    work["_vencimento_ordem"] = work["_vencimento_ordem"].fillna(pd.Timestamp("2262-04-11"))

    # Ordem executiva:
    # 1) renda fixa/compromissadas por vencimento;
    # 2) fundos por liquidez;
    # 3) renda variável/FIIs por liquidação operacional;
    # 4) caixa/saldo.
    is_fundo = work["produto"].astype(str).str.contains("Fundos", case=False, na=False)
    is_rv = work["produto"].astype(str).str.contains("Renda Variável|Fundos Imobiliários|BDRs|ETFs", case=False, na=False, regex=True)
    is_caixa = work["produto"].astype(str).str.contains("Saldo|Caixa", case=False, na=False, regex=True)
    work["_ordem_fluxo"] = np.select([is_fundo, is_rv, is_caixa], [1, 2, 3], default=0)

    return work.sort_values(
        ["titular", "conta", "_ordem_fluxo", "_vencimento_ordem", "_liquidez_ordem", "ativo"],
        ascending=[True, True, True, True, True, True],
    ).drop(columns=["_produto_ordem", "_liquidez_ordem", "_vencimento_ordem", "_ordem_fluxo"], errors="ignore")


def render_detalhamento(positions, summary, reference_date: date):
    section("Detalhamento das contas")

    titulares = ["Todos"] + summary.sort_values("titular")["titular"].tolist()
    selected = st.radio("Titular", titulares, horizontal=True, label_visibility="collapsed")

    df = positions.copy()
    if selected != "Todos":
        df = df[df["titular"] == selected]

    if df.empty:
        st.info("Sem posições para exibir.")
        return

    total = float(df["valor"].sum())
    total_liquido = float(df["valor_liquido"].sum())
    ir_total = float(df["ir"].sum())
    contas = int(df["conta"].nunique())
    posicoes = int(len(df))
    titulo = selected if selected != "Todos" else PARTNER

    st.markdown(
        f"""
        <div class="panel" style="margin-bottom:16px;">
            <div style="display:flex;justify-content:space-between;gap:18px;align-items:flex-start;">
                <div>
                    <div class="section-title" style="margin:0 0 6px 0;">{html.escape(titulo)}</div>
                    <div class="muted">{contas} conta(s) • {posicoes} posição(ões)</div>
                </div>
                <div>
                    <div class="money">{brl(total)}</div>
                    <div class="submoney">Líquido {brl(total_liquido)} • IR {brl(ir_total)}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_eficiencia_fundos(df, reference_date)
    render_eficiencia_compromissadas(df)

    export = df.copy()
    export["Aplicação"] = export["aplicacao"].apply(fmt_date_br)
    export["Vencimento"] = export["vencimento"].apply(fmt_date_br)
    export["Dias até vencimento"] = export["dias_ate_vencimento"].apply(
        lambda x: "" if x is None or pd.isna(x) else int(x)
    )

    export = export[
        [
            "titular",
            "conta",
            "ativo",
            "produto",
            "liquidez",
            "Aplicação",
            "Vencimento",
            "Dias até vencimento",
            "valor_bruto",
            "ir",
            "valor_liquido",
        ]
    ].copy()

    export.columns = [
        "Titular",
        "Conta",
        "Ativo",
        "Produto",
        "Liquidez",
        "Aplicação",
        "Vencimento",
        "Dias até vencimento",
        "Valor bruto",
        "IR",
        "Valor líquido",
    ]

    st.download_button(
        label="baixar arquivo",
        data=to_excel_bytes(export),
        file_name=f"tesouraria_{slugify(str(titulo))}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        help="Baixa a visão filtrada em Excel.",
    )

    view = sort_positions_for_cashflow(df).copy()

    view["Liq."] = view["liquidez"].apply(lambda x: f'<span class="liquidity-pill">{html.escape(str(x))}</span>')
    view["Vence em"] = view["dias_ate_vencimento"].apply(
        lambda x: "—" if x is None or pd.isna(x) else (f"{int(x)}d" if int(x) >= 0 else "vencido")
    )
    view["Part."] = view["participacao_conta"].apply(pct)
    view["Bruto"] = view["valor_bruto"].apply(brl)
    view["IR"] = view["ir"].apply(lambda x: f'<span class="tax-pill">{brl(x)}</span>' if float(x or 0) > 0 else brl(0))
    view["Líquido"] = view["valor_liquido"].apply(brl)

    if "iof_fundo" in view.columns:
        view["IOF"] = view["iof_fundo"].apply(lambda x: "—" if pd.isna(x) else f"{int(x)}%")
    else:
        view["IOF"] = "—"

    table_view = view[
        [
            "titular",
            "conta",
            "ativo",
            "produto",
            "Liq.",
            "aplicacao_fmt",
            "vencimento_fmt",
            "Vence em",
            "IOF",
            "Part.",
            "Bruto",
            "IR",
            "Líquido",
        ]
    ].copy()

    table_view.columns = [
        "Titular",
        "Conta",
        "Ativo",
        "Produto",
        "Liq.",
        "Aplic.",
        "Venc.",
        "Vence em",
        "IOF",
        "Part.",
        "Bruto",
        "IR",
        "Líquido",
    ]

    st.markdown(
        html_table(table_view, allow_html_cols=["Liq.", "IR"], wide=False),
        unsafe_allow_html=True,
    )



def infer_emissor(row):
    asset = str(row.get("ativo", "")).upper()
    produto = str(row.get("produto", ""))

    if "BRADESCO" in asset:
        return "Bradesco"

    if "SAFRA" in asset:
        return "Safra"

    if "BNDES" in asset:
        return "BNDES"

    if "COMPROMISS" in produto.upper() or "COMPROMISS" in asset:
        return "XP / Compromissadas"

    if "SALDO" in asset:
        return "Caixa"

    return produto or "Não identificado"




def load_policy_tables():
    if not POLICY_FILE.exists():
        return {}
    try:
        xl = pd.ExcelFile(POLICY_FILE)
    except Exception:
        return {}
    tables = {}
    for sheet_name in xl.sheet_names:
        try:
            df = pd.read_excel(POLICY_FILE, sheet_name=sheet_name, dtype=str)
            df = df.dropna(how="all")
            if not df.empty:
                tables[sheet_name] = df.fillna("—")
        except Exception:
            pass
    return tables


def parse_pct_cell(value, default=None):
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    text = str(value).strip().replace("%", "").replace(",", ".")
    try:
        num = float(text)
    except Exception:
        return default
    return num / 100 if num > 1 else num


def policy_sheet(tables: dict, name: str) -> pd.DataFrame:
    wanted = normalize_text(name)
    for sheet_name, df in tables.items():
        if normalize_text(sheet_name) == wanted:
            return df.copy()
    return pd.DataFrame()


def get_liquidity_policy_targets(tables: dict):
    """Lê metas mínimas da aba Liquidez, quando existir.

    Retorna mínimos para D+0/D+1 e até D+5. Se a planilha não existir,
    usa fallback conservador compatível com o modelo anterior.
    """
    liq = policy_sheet(tables, "Liquidez")
    targets = {
        "d1_min": 0.80,
        "d5_min": None,
    }
    if liq.empty:
        return targets

    cols = {normalize_text(c): c for c in liq.columns}
    faixa_col = cols.get("faixa de liquidez") or cols.get("faixa") or cols.get("liquidez")
    min_col = cols.get("alocacao minima") or cols.get("alocacao mínima") or cols.get("minimo") or cols.get("mínimo")
    prazo_col = cols.get("prazo de resgate") or cols.get("prazo")
    if not min_col:
        return targets

    for _, row in liq.iterrows():
        faixa = normalize_text(row.get(faixa_col, "")) if faixa_col else ""
        prazo = normalize_text(row.get(prazo_col, "")) if prazo_col else ""
        minimum = parse_pct_cell(row.get(min_col), None)
        if minimum is None:
            continue
        if "imediata" in faixa or "d+1" in prazo:
            targets["d1_min"] = minimum
        if "curta" in faixa or "d+5" in prazo or "ate d+5" in prazo:
            targets["d5_min"] = minimum
    return targets


def render_policy_config_tables():
    tables = load_policy_tables()
    if not tables:
        return False

    allowed = ["Liquidez", "Produtos", "Concentracao", "Concentração"]
    rendered = False
    section("Política configurada")
    for sheet_name, df in tables.items():
        sheet_norm = normalize_text(sheet_name)
        if sheet_norm not in [normalize_text(x) for x in allowed]:
            continue
        if df.empty:
            continue
        rendered = True
        st.markdown(
            f'<div class="section-title" style="margin-top:10px;">{html.escape(str(sheet_name))}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(html_table(df, wide=False), unsafe_allow_html=True)
    return rendered


def render_horarios_operacionais():
    tables = load_policy_tables()
    horarios = policy_sheet(tables, "Horarios")

    section("Horários operacionais")

    if not horarios.empty:
        cols = {normalize_text(c): c for c in horarios.columns}
        inst_col = cols.get("instituicao") or cols.get("instituição")
        if inst_col:
            inst_values = [x for x in horarios[inst_col].dropna().unique().tolist() if str(x).strip()]
            if len(inst_values) >= 2:
                columns = st.columns(min(len(inst_values), 3))
                for idx, inst in enumerate(inst_values[:3]):
                    with columns[idx]:
                        df_inst = horarios[horarios[inst_col].astype(str) == str(inst)].drop(columns=[inst_col], errors="ignore")
                        st.markdown(
                            f'<div class="section-title" style="margin-top:0;">{html.escape(str(inst))}</div>',
                            unsafe_allow_html=True,
                        )
                        st.markdown(html_table(df_inst, wide=False), unsafe_allow_html=True)
                return
        st.markdown(html_table(horarios, wide=False), unsafe_allow_html=True)
        return

    xp = pd.DataFrame(
        [
            ["Aplicação", "Emissão Bancária Primária", "10h00 às 15h00"],
            ["Aplicação", "Emissão Bancária Secundária", "10h00 às 17h30"],
            ["Aplicação", "Crédito Privado", "10h00 às 17h30"],
            ["Aplicação", "Títulos Públicos", "10h00 às 17h00"],
            ["Aplicação", "Compromissadas", "10h00 às 17h30"],
            ["Resgate", "Emissão Bancária Primária", "10h00 às 17h00"],
            ["Resgate", "Emissão Bancária Secundária", "10h00 às 17h00"],
            ["Resgate", "Crédito Privado", "10h00 às 15h00"],
            ["Resgate", "Títulos Públicos", "10h00 às 17h00"],
            ["Resgate", "Compromissadas", "08h00 às 16h15"],
        ],
        columns=["Tipo", "Produto", "XP"],
    )

    btg = pd.DataFrame(
        [
            ["Aplicação", "Emissão Bancária Primária", "10h00 às 15h00"],
            ["Aplicação", "Emissão Bancária Secundária", "10h00 às 15h00"],
            ["Aplicação", "Crédito Privado", "10h00 às 16h00"],
            ["Aplicação", "Títulos Públicos", "10h00 às 15h00"],
            ["Aplicação", "Compromissadas", "10h00 às 17h00"],
            ["Resgate", "Emissão Bancária Primária", "10h00 às 15h00"],
            ["Resgate", "Emissão Bancária Secundária", "10h00 às 16h00"],
            ["Resgate", "Crédito Privado", "10h00 às 16h00"],
            ["Resgate", "Títulos Públicos", "10h00 às 15h00"],
            ["Resgate", "Compromissadas", "08h00 às 17h00"],
        ],
        columns=["Tipo", "Produto", "BTG"],
    )

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(html_table(xp, wide=False), unsafe_allow_html=True)
    with col2:
        st.markdown(html_table(btg, wide=False), unsafe_allow_html=True)


def get_concentration_policy_limit_pct(tables: dict, default_pct=LIMITE_EMISSOR_PCT):
    """Busca um limite percentual na aba Concentração/Concentracao.

    Prioridade: linha 'Por ativo'; depois linhas com 'nível 1' ou limite numérico.
    Retorna percentual decimal, ex.: 25% -> 0.25.
    """
    conc = policy_sheet(tables, "Concentracao")
    if conc.empty:
        conc = policy_sheet(tables, "Concentração")
    if conc.empty:
        return default_pct

    cols = {normalize_text(c): c for c in conc.columns}
    limite_col = cols.get("limite") or cols.get("limites maximo de concentracao") or cols.get("limites máximo de concentração")
    max_col = cols.get("maximo") or cols.get("máximo") or cols.get("alocacao maxima") or cols.get("alocação máxima")
    if not max_col:
        return default_pct

    candidates = []
    for _, row in conc.iterrows():
        label = normalize_text(row.get(limite_col, "")) if limite_col else ""
        val = parse_pct_cell(row.get(max_col), None)
        if val is None:
            continue
        if "por ativo" in label:
            return val
        if "nivel 1" in label or "nível 1" in label:
            candidates.append((0, val))
        else:
            candidates.append((1, val))
    if candidates:
        candidates = sorted(candidates, key=lambda x: x[0])
        return candidates[0][1]
    return default_pct


def render_politica(positions, kpis):
    section("Política de investimentos")

    tables = load_policy_tables()
    targets = get_liquidity_policy_targets(tables)

    d1_value = float(positions[positions["liquidez"].apply(liquidity_to_days) <= 1]["valor"].sum())
    d5_value = float(positions[positions["liquidez"].apply(liquidity_to_days) <= 5]["valor"].sum())
    d1_pct = safe_div(d1_value, kpis["total"])
    d5_pct = safe_div(d5_value, kpis["total"])

    concentration_limit_pct = get_concentration_policy_limit_pct(tables)
    concentration_limit_value = kpis["total"] * concentration_limit_pct

    emissor_check = positions.copy()
    emissor_check["emissor"] = emissor_check.apply(infer_emissor, axis=1)
    top_concentration_value = float(emissor_check.groupby("emissor")["valor"].sum().max()) if not emissor_check.empty else 0.0
    top_concentration_pct = safe_div(top_concentration_value, kpis["total"])

    checks_rows = [
        [
            "Liquidez imediata",
            f"D+0/D+1 mínimo {pct(targets['d1_min'])}",
            status_badge(d1_pct >= targets["d1_min"]),
            pct(d1_pct),
        ],
    ]
    if targets.get("d5_min") is not None:
        checks_rows.append(
            [
                "Liquidez curta acumulada",
                f"Até D+5 mínimo {pct(targets['d5_min'])}",
                status_badge(d5_pct >= targets["d5_min"]),
                pct(d5_pct),
            ]
        )
    checks_rows.extend(
        [
            [
                "Concentração máxima",
                f"Limite configurado {pct(concentration_limit_pct)} do PL",
                status_badge(top_concentration_pct <= concentration_limit_pct),
                pct(top_concentration_pct),
            ],
            [
                "IR consolidado",
                "Diferença entre posição bruta e valor líquido",
                status_badge(True),
                brl(kpis["ir_total"]),
            ],
        ]
    )

    checks = pd.DataFrame(checks_rows, columns=["Controle", "Regra", "Status", "Leitura"])
    st.markdown(html_table(checks, allow_html_cols=["Status"], wide=False), unsafe_allow_html=True)

    render_policy_config_tables()

    section("Limite por produto / emissor")

    limite_emissor = concentration_limit_value

    emissor_df = positions.copy()
    emissor_df["emissor"] = emissor_df.apply(infer_emissor, axis=1)

    emissores = emissor_df.groupby("emissor", as_index=False).agg(
        valor=("valor", "sum"),
        valor_liquido=("valor_liquido", "sum"),
        ir=("ir", "sum"),
    ).sort_values("valor", ascending=False)

    emissores["% carteira"] = emissores["valor"] / kpis["total"]
    emissores["limite"] = limite_emissor
    emissores["status"] = emissores["valor"].apply(lambda v: status_badge(v <= limite_emissor))

    emissores["% carteira"] = emissores["% carteira"].apply(pct)
    emissores["limite"] = emissores["limite"].apply(brl)
    emissores["valor bruto"] = emissores["valor"].apply(brl)
    emissores["IR"] = emissores["ir"].apply(brl)
    emissores["valor líquido"] = emissores["valor_liquido"].apply(brl)

    emissores = emissores[["emissor", "valor bruto", "% carteira", "limite", "IR", "valor líquido", "status"]]
    emissores.columns = ["Produto / Emissor", "Bruto", "% cart.", "Limite", "IR", "Líquido", "Status"]
    st.markdown(html_table(emissores, allow_html_cols=["Status"], wide=False), unsafe_allow_html=True)

    render_horarios_operacionais()

    st.markdown(
        '<div class="muted" style="margin-top:12px;">Aplicações feitas fora da janela de funcionamento ficam agendadas para o dia útil seguinte, sujeitas à disponibilidade do ativo nas mesmas condições. Horários de Brasília.</div>',
        unsafe_allow_html=True,
    )


def main():
    page_icon = Image.open(BOTUVERA_LOGO) if BOTUVERA_LOGO.exists() else "📊"

    st.set_page_config(
        page_title=APP_TITLE,
        page_icon=page_icon,
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    inject_css()

    with st.sidebar:
        st.markdown("### Atualização de dados")
        st.caption("Use os arquivos em `data/positions/` ou faça upload manual para conferência.")
        uploaded = st.file_uploader(
            "Upload manual de posições XP",
            type=["xlsx"],
            accept_multiple_files=True,
        )

        st.divider()
        st.markdown("### Configurações")
        st.write("• Política: `data/config/politica_investimentos.xlsx`")
        st.write("• Fundos: `data/config/fundos_mapeamento.xlsx`")
        st.write("• IOF de fundos: 96% no 1º dia e zeragem no 30º dia corrido")

    if uploaded:
        positions, summary = load_data_from_uploads(uploaded)
    else:
        positions, summary = load_data_from_disk(get_mtime_token())

    if positions.empty or summary.empty:
        render_header("—")
        st.error(
            "Nenhuma posição encontrada. Inclua arquivos `.xlsx` em `data/positions/` ou use o upload manual na lateral."
        )
        return

    ref_dates = pd.to_datetime(summary["data_referencia"], errors="coerce").dropna()
    reference_date = ref_dates.max().date() if not ref_dates.empty else date.today()

    positions, summary = enrich(positions, summary, reference_date)
    kpis = calc_kpis(positions, summary)

    render_header(fmt_date_br(reference_date))

    tabs = st.tabs(
        [
            "Visão Geral",
            "Detalhamento das Contas",
            "Política de Investimentos",
        ]
    )

    with tabs[0]:
        render_visao_geral(positions, summary, kpis)

    with tabs[1]:
        render_detalhamento(positions, summary, reference_date)

    with tabs[2]:
        render_politica(positions, kpis)

    st.markdown(
        f'<div class="footer">{GESTOR} • Tesouraria {PARTNER} • Informações confidenciais</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
