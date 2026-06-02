import html
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None

try:
    import extra_streamlit_components as stx
    COOKIE_AVAILABLE = True
except Exception:
    stx = None
    COOKIE_AVAILABLE = False

try:
    from streamlit_extras.stylable_container import stylable_container
    STYLABLE_CONTAINER_AVAILABLE = True
except Exception:
    stylable_container = None
    STYLABLE_CONTAINER_AVAILABLE = False


# ============================================================
# 기본 설정
# ============================================================

st.set_page_config(
    page_title="연구실 시약관리",
    page_icon="🧪",
    layout="wide",
)

KST = ZoneInfo("Asia/Seoul")

REAGENT_COLUMNS = [
    "id",
    "name",
    "volume",
    "stock_count",
    "waiting_count",
    "created_at",
    "updated_at",
]

LOG_COLUMNS = [
    "timestamp",
    "user",
    "category",
    "reagent",
    "delta",
    "message",
]

LOCAL_DATA_DIR = Path("data")
LOCAL_REAGENTS_FILE = LOCAL_DATA_DIR / "reagents.csv"
LOCAL_LOGS_FILE = LOCAL_DATA_DIR / "logs.csv"

CARD_COLORS = [
    {"bg": "#EAF4FF", "border": "#BBD9FF"},  # light blue
    {"bg": "#FFF1F1", "border": "#FFC9C9"},  # light red
    {"bg": "#F0FFF4", "border": "#BFE8C7"},  # light green
    {"bg": "#FFF9E8", "border": "#F3DFA3"},  # light yellow
]


# ============================================================
# 공통 유틸 함수
# ============================================================

def now_text() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def make_id() -> str:
    return uuid.uuid4().hex[:10]


def safe_text(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def to_int(value, default: int = 0) -> int:
    try:
        if pd.isna(value):
            return default
        return int(float(value))
    except Exception:
        return default


def count_color(count: int) -> str:
    if count <= 1:
        return "#d32f2f"
    if count <= 3:
        return "#f57c00"
    return "#2e7d32"


def normalize_reagents(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=REAGENT_COLUMNS)

    for col in REAGENT_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df = df[REAGENT_COLUMNS].copy()
    df["id"] = df["id"].astype(str)
    df["name"] = df["name"].astype(str)
    df["volume"] = df["volume"].astype(str)
    df["stock_count"] = df["stock_count"].apply(lambda x: max(0, to_int(x)))
    df["waiting_count"] = df["waiting_count"].apply(lambda x: max(0, to_int(x)))
    df["created_at"] = df["created_at"].astype(str)
    df["updated_at"] = df["updated_at"].astype(str)

    empty_id = df["id"].str.strip() == ""
    if empty_id.any():
        df.loc[empty_id, "id"] = [make_id() for _ in range(empty_id.sum())]

    return df


def normalize_logs(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=LOG_COLUMNS)

    for col in LOG_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df = df[LOG_COLUMNS].copy()
    for col in LOG_COLUMNS:
        df[col] = df[col].astype(str)
    return df


# ============================================================
# 사용자 이름 저장: 브라우저 쿠키 사용
# ============================================================

def get_cookie_manager():
    if not COOKIE_AVAILABLE:
        return None
    return stx.CookieManager()


def get_current_user() -> str:
    cookie_manager = get_cookie_manager()

    saved_name = ""
    if cookie_manager is not None:
        try:
            saved_name = cookie_manager.get("lab_reagent_user") or ""
        except Exception:
            saved_name = ""

    if "user_name" not in st.session_state:
        st.session_state["user_name"] = saved_name

    user_name = st.text_input(
        "사용자",
        key="user_name",
        placeholder="예: 전자파",
        help="회원가입 없이 로그에 남길 이름만 입력합니다. 같은 브라우저에서는 쿠키로 유지됩니다.",
    ).strip()

    if cookie_manager is not None and user_name:
        try:
            cookie_manager.set(
                "lab_reagent_user",
                user_name,
                expires_at=datetime.now() + timedelta(days=365),
            )
        except Exception:
            pass

    return user_name


def require_user(user_name: str) -> bool:
    if not user_name:
        st.warning("먼저 오른쪽 상단의 사용자 이름을 입력해 주세요.")
        return False
    return True


# ============================================================
# 저장소 설정: Google Sheets 우선, 없으면 로컬 CSV
# ============================================================

def has_google_sheet_secrets() -> bool:
    try:
        return bool(st.secrets.get("spreadsheet_id")) and bool(st.secrets.get("gcp_service_account"))
    except Exception:
        return False


USE_GOOGLE_SHEETS = has_google_sheet_secrets()


@st.cache_resource
def get_google_worksheets():
    if not USE_GOOGLE_SHEETS:
        return None, None

    if gspread is None or Credentials is None:
        st.error("Google Sheets 사용을 위해 gspread, google-auth 설치가 필요합니다.")
        st.stop()

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scopes,
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(st.secrets["spreadsheet_id"])

    def get_or_create_ws(title: str, columns: list[str]):
        try:
            ws = spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=max(len(columns), 5))
            ws.update(values=[columns], range_name="A1")
            return ws

        header = ws.row_values(1)
        if not header:
            ws.update(values=[columns], range_name="A1")
        return ws

    reagents_ws = get_or_create_ws("Reagents", REAGENT_COLUMNS)
    logs_ws = get_or_create_ws("Logs", LOG_COLUMNS)

    return reagents_ws, logs_ws


def load_reagents() -> pd.DataFrame:
    if USE_GOOGLE_SHEETS:
        reagents_ws, _ = get_google_worksheets()
        records = reagents_ws.get_all_records()
        return normalize_reagents(pd.DataFrame(records))

    LOCAL_DATA_DIR.mkdir(exist_ok=True)
    if not LOCAL_REAGENTS_FILE.exists():
        df = pd.DataFrame(columns=REAGENT_COLUMNS)
        df.to_csv(LOCAL_REAGENTS_FILE, index=False, encoding="utf-8-sig")
        return df

    return normalize_reagents(pd.read_csv(LOCAL_REAGENTS_FILE))


def save_reagents(df: pd.DataFrame) -> None:
    df = normalize_reagents(df)

    if USE_GOOGLE_SHEETS:
        reagents_ws, _ = get_google_worksheets()
        values = [REAGENT_COLUMNS] + df.astype(str).values.tolist()
        reagents_ws.clear()
        reagents_ws.update(values=values, range_name="A1")
        return

    LOCAL_DATA_DIR.mkdir(exist_ok=True)
    df.to_csv(LOCAL_REAGENTS_FILE, index=False, encoding="utf-8-sig")


def load_logs() -> pd.DataFrame:
    if USE_GOOGLE_SHEETS:
        _, logs_ws = get_google_worksheets()
        records = logs_ws.get_all_records()
        return normalize_logs(pd.DataFrame(records))

    LOCAL_DATA_DIR.mkdir(exist_ok=True)
    if not LOCAL_LOGS_FILE.exists():
        df = pd.DataFrame(columns=LOG_COLUMNS)
        df.to_csv(LOCAL_LOGS_FILE, index=False, encoding="utf-8-sig")
        return df

    return normalize_logs(pd.read_csv(LOCAL_LOGS_FILE))


def append_log(user: str, category: str, reagent: str, delta: int, message: str) -> None:
    row = [now_text(), user, category, reagent, str(delta), message]

    if USE_GOOGLE_SHEETS:
        _, logs_ws = get_google_worksheets()
        logs_ws.append_row(row, value_input_option="USER_ENTERED")
        return

    LOCAL_DATA_DIR.mkdir(exist_ok=True)
    logs = load_logs()
    logs.loc[len(logs)] = row
    logs.to_csv(LOCAL_LOGS_FILE, index=False, encoding="utf-8-sig")


# ============================================================
# 데이터 변경 함수
# ============================================================

def update_count(reagent_id: str, field: str, delta: int, user_name: str) -> None:
    df = load_reagents()
    idx_list = df.index[df["id"] == reagent_id].tolist()

    if not idx_list:
        st.error("해당 시약을 찾을 수 없습니다. 새로고침 후 다시 시도해 주세요.")
        return

    idx = idx_list[0]
    name = df.at[idx, "name"]
    volume = df.at[idx, "volume"]

    current_value = to_int(df.at[idx, field])
    new_value = max(0, current_value + delta)
    actual_delta = new_value - current_value

    df.at[idx, field] = new_value
    df.at[idx, "updated_at"] = now_text()
    save_reagents(df)

    abs_delta = abs(actual_delta)

    if field == "stock_count":
        if actual_delta < 0:
            message = f"{user_name}님이 {name} ({volume}) 시약을 {abs_delta}통 사용했습니다. 남은 수량: {new_value}통"
            category = "사용"
        elif actual_delta > 0:
            message = f"{user_name}님이 {name} ({volume}) 시약 재고를 {abs_delta}통 증가시켰습니다. 남은 수량: {new_value}통"
            category = "재고 증가"
        else:
            message = f"{user_name}님이 {name} ({volume}) 시약 재고 변경을 시도했지만 수량 변화가 없었습니다. 남은 수량: {new_value}통"
            category = "재고 변경 없음"

    elif field == "waiting_count":
        if actual_delta > 0:
            message = f"{user_name}님이 {name} ({volume}) 시약을 {abs_delta}통 주문했습니다. 배송 대기: {new_value}통"
            category = "주문"
        elif actual_delta < 0:
            message = f"{user_name}님이 {name} ({volume}) 시약의 배송 대기 수량을 {abs_delta}통 감소시켰습니다. 배송 대기: {new_value}통"
            category = "주문 조정"
        else:
            message = f"{user_name}님이 {name} ({volume}) 시약 주문 수량 변경을 시도했지만 수량 변화가 없었습니다. 배송 대기: {new_value}통"
            category = "주문 변경 없음"
    else:
        message = f"{user_name}님이 {name} ({volume}) 수량을 변경했습니다."
        category = "변경"

    append_log(user_name, category, f"{name} ({volume})", actual_delta, message)


def receive_count(reagent_id: str, amount: int, user_name: str) -> None:
    df = load_reagents()
    idx_list = df.index[df["id"] == reagent_id].tolist()

    if not idx_list:
        st.error("해당 시약을 찾을 수 없습니다. 새로고침 후 다시 시도해 주세요.")
        return

    idx = idx_list[0]
    waiting = to_int(df.at[idx, "waiting_count"])
    receive_amount = min(max(0, int(amount)), waiting)

    if receive_amount <= 0:
        st.warning("배송 대기 수량이 없습니다.")
        return

    name = df.at[idx, "name"]
    volume = df.at[idx, "volume"]

    df.at[idx, "waiting_count"] = waiting - receive_amount
    df.at[idx, "stock_count"] = to_int(df.at[idx, "stock_count"]) + receive_amount
    df.at[idx, "updated_at"] = now_text()
    save_reagents(df)

    message = (
        f"{user_name}님이 {name} ({volume}) 시약 {receive_amount}통을 입고 처리했습니다. "
        f"남은 수량: {df.at[idx, 'stock_count']}통, 배송 대기: {df.at[idx, 'waiting_count']}통"
    )
    append_log(user_name, "입고", f"{name} ({volume})", receive_amount, message)


def add_reagent(name: str, volume: str, stock_count: int, waiting_count: int, user_name: str) -> None:
    df = load_reagents()
    created = now_text()

    new_row = {
        "id": make_id(),
        "name": name.strip(),
        "volume": volume.strip(),
        "stock_count": max(0, int(stock_count)),
        "waiting_count": max(0, int(waiting_count)),
        "created_at": created,
        "updated_at": created,
    }

    df.loc[len(df)] = new_row
    save_reagents(df)

    message = (
        f"{user_name}님이 신규 시약 {new_row['name']} ({new_row['volume']})을 등록했습니다. "
        f"남은 수량: {new_row['stock_count']}통, 배송 대기: {new_row['waiting_count']}통"
    )
    append_log(user_name, "신규 등록", f"{new_row['name']} ({new_row['volume']})", stock_count, message)


# ============================================================
# 화면 디자인
# ============================================================

st.markdown(
    """
    <style>
    html, body, [data-testid="stAppViewContainer"] {
        overflow-y: auto !important;
    }
    .block-container {
        padding-top: 3.2rem !important;
        padding-bottom: 5.0rem !important;
        max-width: 100% !important;
    }
    .main-title {
        font-size: 2.0rem;
        font-weight: 800;
        margin-bottom: 0.2rem;
    }
    .sub-text {
        color: #666;
        font-size: 0.95rem;
        margin-bottom: 1.2rem;
    }
    .reagent-name {
        font-weight: 900;
        font-size: 1.25rem;
        margin-bottom: 0.35rem;
        color: #0f172a;
    }
    .reagent-volume {
        color: #334155;
        font-size: 1.02rem;
        font-weight: 650;
    }
    .count-number {
        font-size: 1.75rem;
        font-weight: 900;
        line-height: 1.1;
    }
    .count-title {
        font-weight: 800;
        color: #111827;
        margin-bottom: 0.15rem;
    }
    .count-label {
        color: #475569;
        font-size: 0.85rem;
    }
    .small-muted {
        color: #64748b;
        font-size: 0.85rem;
    }
    div[data-testid="stButton"] > button {
        height: 2.15rem;
        border-radius: 10px;
        background-color: rgba(255, 255, 255, 0.86) !important;
        border: 1px solid #d1d5db !important;
        color: #111827 !important;
        box-shadow: none !important;
    }
    div[data-testid="stButton"] > button:hover {
        border: 1px solid #94a3b8 !important;
        background-color: rgba(255, 255, 255, 0.98) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# 메인 화면
# ============================================================

left, right = st.columns([2, 1])
with left:
    st.markdown('<div class="main-title">🧪 연구실 시약관리</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub-text">팀 내부에서 빠르게 재고, 주문 대기, 사용 로그를 확인하기 위한 간단한 관리 페이지입니다.</div>',
        unsafe_allow_html=True,
    )

with right:
    user_name = get_current_user()

storage_badge = "Google Sheets 공유 저장소" if USE_GOOGLE_SHEETS else "로컬 CSV 테스트 모드"
st.caption(f"현재 저장 방식: {storage_badge}")

if not USE_GOOGLE_SHEETS:
    st.info(
        "현재 온라인 모드로 실행 중입니다. "
        "팀원들과 동일한 데이터를 공유합니다."
    )

df = load_reagents()

tab1, tab2 = st.tabs(["시약 현황", "신규 시약 등록"])


# ------------------------------------------------------------
# 1. 시약 현황
# ------------------------------------------------------------
with tab1:
    st.subheader("시약 현황")
    st.caption("재고와 주문/배송 대기를 한 카드 안에서 바로 확인하고 조절합니다.")

    if not STYLABLE_CONTAINER_AVAILABLE:
        st.warning("카드 색상 표시를 위해 streamlit-extras 설치가 필요합니다. cmd에서 'py -m pip install streamlit-extras'를 실행해 주세요.")

    if df.empty:
        st.warning("아직 등록된 시약이 없습니다. 먼저 신규 시약을 등록해 주세요.")
    else:
        search = st.text_input("시약 검색", placeholder="예: Acetone, Ethanol, DMF", key="status_search").strip().lower()
        shown = df.copy()
        if search:
            shown = shown[
                shown["name"].str.lower().str.contains(search, na=False)
                | shown["volume"].str.lower().str.contains(search, na=False)
            ]

        if shown.empty:
            st.info("검색 결과가 없습니다.")

        for card_idx, (_, row) in enumerate(shown.sort_values(["name", "volume"]).iterrows()):
            rid = row["id"]
            stock = to_int(row["stock_count"])
            waiting = to_int(row["waiting_count"])
            stock_color = count_color(stock)
            card_color = CARD_COLORS[card_idx % len(CARD_COLORS)]

            card_css = f"""
            {{
                background-color: {card_color['bg']} !important;
                border: 1px solid {card_color['border']} !important;
                border-radius: 16px !important;
                padding: 18px 18px 16px 18px !important;
                margin: 12px 0 14px 0 !important;
                box-shadow: 0 1px 5px rgba(15, 23, 42, 0.06) !important;
            }}
            """

            container_key = f"reagent_card_{rid}_{card_idx}"
            if STYLABLE_CONTAINER_AVAILABLE:
                card_context = stylable_container(key=container_key, css_styles=card_css)
            else:
                card_context = st.container(border=True)

            with card_context:
                c_name, c_stock, c_stock_btns, c_waiting, c_waiting_btns = st.columns([2.1, 0.8, 2.5, 0.8, 2.8])

                with c_name:
                    st.markdown(
                        f"<div class='reagent-name'>{safe_text(row['name'])}</div>"
                        f"<div class='reagent-volume'>용량: {safe_text(row['volume'])}</div>",
                        unsafe_allow_html=True,
                    )

                with c_stock:
                    st.markdown(
                        f"<div class='count-title'>현재 재고</div>"
                        f"<div class='count-number' style='color:{stock_color};'>{stock}통</div>"
                        f"<div class='count-label'>남은 수량</div>",
                        unsafe_allow_html=True,
                    )

                with c_stock_btns:
                    minus_cols = st.columns(3)
                    for col, amount in zip(minus_cols, [1, 5, 10]):
                        with col:
                            if st.button(f"−{amount}통", key=f"stock_minus_{amount}_{rid}", use_container_width=True):
                                if require_user(user_name):
                                    update_count(rid, "stock_count", -amount, user_name)
                                    st.rerun()
                    plus_cols = st.columns(3)
                    for col, amount in zip(plus_cols, [1, 5, 10]):
                        with col:
                            if st.button(f"+{amount}통", key=f"stock_plus_{amount}_{rid}", use_container_width=True):
                                if require_user(user_name):
                                    update_count(rid, "stock_count", amount, user_name)
                                    st.rerun()

                with c_waiting:
                    st.markdown(
                        f"<div class='count-title'>배송 대기</div>"
                        f"<div class='count-number' style='color:#111827;'>{waiting}통</div>"
                        f"<div class='count-label'>주문 수량</div>",
                        unsafe_allow_html=True,
                    )

                with c_waiting_btns:
                    minus_cols = st.columns(3)
                    for col, amount in zip(minus_cols, [1, 5, 10]):
                        with col:
                            if st.button(f"−{amount}통", key=f"wait_minus_{amount}_{rid}", use_container_width=True):
                                if require_user(user_name):
                                    update_count(rid, "waiting_count", -amount, user_name)
                                    st.rerun()
                    plus_cols = st.columns(3)
                    for col, amount in zip(plus_cols, [1, 5, 10]):
                        with col:
                            if st.button(f"+{amount}통", key=f"wait_plus_{amount}_{rid}", use_container_width=True):
                                if require_user(user_name):
                                    update_count(rid, "waiting_count", amount, user_name)
                                    st.rerun()
                    receive_cols = st.columns(2)
                    with receive_cols[0]:
                        if st.button("입고 처리 +1통", key=f"receive_1_{rid}", use_container_width=True):
                            if require_user(user_name):
                                receive_count(rid, 1, user_name)
                                st.rerun()
                    with receive_cols[1]:
                        if st.button("입고 처리 +5통", key=f"receive_5_{rid}", use_container_width=True):
                            if require_user(user_name):
                                receive_count(rid, 5, user_name)
                                st.rerun()


# ------------------------------------------------------------
# 2. 신규 시약 등록
# ------------------------------------------------------------
with tab2:
    st.subheader("신규 시약 등록")

    with st.form("new_reagent_form", clear_on_submit=True):
        name = st.text_input("시약 이름", placeholder="예: Acetone")
        volume = st.text_input("용량", placeholder="예: 500 mL, 1 L, 100 g")
        stock_count = st.number_input("현재 남은 수량", min_value=0, value=0, step=1)
        waiting_count = st.number_input("배송 대기 수량", min_value=0, value=0, step=1)

        submitted = st.form_submit_button("신규 시약 등록", use_container_width=True)

    if submitted:
        if not require_user(user_name):
            st.stop()

        if not name.strip():
            st.error("시약 이름을 입력해 주세요.")
        elif not volume.strip():
            st.error("용량을 입력해 주세요.")
        else:
            add_reagent(name, volume, stock_count, waiting_count, user_name)
            st.success(f"{name.strip()} 시약을 등록했습니다.")
            st.rerun()


# ------------------------------------------------------------
# 3. 로그 확인
# ------------------------------------------------------------
st.divider()

with st.expander("로그 확인", expanded=False):
    logs = load_logs()

    if logs.empty:
        st.info("아직 로그가 없습니다.")
    else:
        logs = logs.sort_values("timestamp", ascending=False).head(100)
        st.caption("최근 100개 로그만 표시합니다.")

        for _, row in logs.iterrows():
            st.markdown(
                f"**{safe_text(row['timestamp'])}**  \\n"
                f"{safe_text(row['message'])}"
            )
            st.divider()
