import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import calendar
from io import BytesIO
st.set_page_config(page_title='GitHub Billing Portal', layout='wide')

def safe_format_date(date_val):
    if not date_val:
        return None
    try:
        if isinstance(date_val, str):
            return date_val.split('T')[0]
        elif isinstance(date_val, (int, float)):
            ts = date_val / 1000 if date_val > 10**11 else date_val
            return datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
    except Exception:
        return 'N/A'
    return 'N/A'

def paginate_api(url, headers, key=None):
    results = []
    next_url = url
    while next_url:
        try:
            res = requests.get(next_url, headers=headers, timeout=15)
            res.raise_for_status()
            data = res.json()
            if key:
                results.extend(data.get(key, []))
            elif isinstance(data, list):
                results.extend(data)
            next_url = None
            link_header = res.headers.get('Link', '')
            for part in link_header.split(','):
                part = part.strip()
                if 'rel="next"' in part:
                    next_url = part.split(';')[0].strip().strip('<>')
                    break
        except requests.exceptions.HTTPError:
            st.warning(f'페이지 요청 실패 ({res.status_code}): {next_url}')
            break
        except requests.exceptions.RequestException as e:
            st.warning(f'요청 실패: {str(e)}')
            break
    return results

def fetch_data(year, start_month, end_month, ent_name, org_name, headers):
    start_date = f'{year}-{start_month:02d}-01'
    _, last_day = calendar.monthrange(year, end_month)
    end_date = f'{year}-{end_month:02d}-{last_day}'
    errors = []
    audit_url = (
        f'https://api.github.com/enterprises/{ent_name}/audit-log'
        f'?phrase=created:{start_date}..{end_date}'
        f'&per_page=100'
    )
    audit_res = paginate_api(audit_url, headers)
    members_url = f'https://api.github.com/orgs/{org_name}/members?per_page=100'
    members_data = paginate_api(members_url, headers)
    if members_data is not None:
        active_mems = set(m['login'] for m in members_data if 'login' in m)
    else:
        active_mems = set()
        errors.append('멤버 목록 조회 실패')
    cp_url = (
        f'https://api.github.com/enterprises/{ent_name}'
        f'/copilot/billing/seats?per_page=100'
    )
    cp_data = paginate_api(cp_url, headers, key='seats')
    if cp_data is not None:
        cp_seats = set(
            s['assignee']['login'] for s in cp_data
            if s.get('assignee') and 'login' in s['assignee']
        )
    else:
        cp_seats = set()
        errors.append('Copilot 시트 조회 실패')
    ghec_events = {}
    cp_events = {}
    for log in audit_res:
        user = log.get('user') or log.get('assignee')
        action = log.get('action', '')
        ts = log.get('created_at')
        if not user or not isinstance(user, str):
            continue
        if 'member' in action:
            if user not in ghec_events:
                ghec_events[user] = {'added': None, 'removed': None}
            if 'add' in action or 'invite' in action:
                ghec_events[user]['added'] = ts
            elif 'remove' in action:
                ghec_events[user]['removed'] = ts
        elif 'copilot' in action:
            if user not in cp_events:
                cp_events[user] = {'added': None, 'removed': None}
            if 'assign_seat' in action:
                cp_events[user]['added'] = ts
            elif 'revoke_seat' in action:
                cp_events[user]['removed'] = ts
    def ghec_status(user, ev):
        removed_date = safe_format_date(ev['removed'])
        if removed_date and removed_date <= end_date:
            return 'Deleted'
        return 'Active' if user in active_mems else 'Deleted'
    def cp_status(user, ev):
        removed_date = safe_format_date(ev['removed'])
        if removed_date and removed_date <= end_date:
            return 'Revoked'
        return 'Active' if user in cp_seats else 'Revoked'
    cols = ['User ID', 'Status', 'Added', 'Removed']
    ghec_rows = [
        {'User ID': u, 'Status': ghec_status(u, ev),
         'Added': safe_format_date(ev['added']), 'Removed': safe_format_date(ev['removed'])}
        for u, ev in ghec_events.items()
    ]
    cp_rows = [
        {'User ID': u, 'Status': cp_status(u, ev),
         'Added': safe_format_date(ev['added']), 'Removed': safe_format_date(ev['removed'])}
        for u, ev in cp_events.items()
    ]
    ghec_df = pd.DataFrame(ghec_rows) if ghec_rows else pd.DataFrame(columns=cols)
    cp_df = pd.DataFrame(cp_rows) if cp_rows else pd.DataFrame(columns=cols)
    return ghec_df, cp_df, errors

st.title('GitHub Enterprise 빌링 리포트 서비스')
st.markdown('엔터프라이즈 라이선스 사용 현황을 실시간으로 조회하고 엑셀로 추출합니다.')

with st.sidebar:
    st.header('설정')
    token = st.text_input('GitHub Token', type='password', help='ghp_... 형식의 PAT 입력')
    ent_name = st.text_input('Enterprise Name', value='esse-git')
    org_name = st.text_input('Organization Name', value='essegrg001')
    st.header('조회 설정')
    target_year = st.selectbox('년도 선택', [2024, 2025, 2026], index=2)
    month_range = st.slider('월 선택 (범위)', 1, 12, (1, 12))
    btn_run = st.button('데이터 불러오기')

if btn_run:
    if not token:
        st.error('사이드바에서 GitHub Token을 입력해주세요.')
    else:
        hdrs = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'}
        with st.spinner('GitHub API에서 데이터를 불러오는 중...'):
            ghec_df, cp_df, errors = fetch_data(target_year, month_range[0], month_range[1], ent_name, org_name, hdrs)
        if errors:
            for err in errors:
                st.warning(f'⚠️ {err}')
        col1, col2 = st.columns(2)
        with col1:
            st.subheader('GHEC 사용 현황')
            st.caption(f'총 {len(ghec_df)}건')
            st.dataframe(ghec_df, use_container_width=True)
        with col2:
            st.subheader('Copilot 사용 현황')
            st.caption(f'총 {len(cp_df)}건')
            st.dataframe(cp_df, use_container_width=True)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            ghec_df.to_excel(writer, index=False, sheet_name='GHEC')
            cp_df.to_excel(writer, index=False, sheet_name='Copilot')
        st.download_button(
            label='엑셀 리포트 다운로드',
            data=output.getvalue(),
            file_name=f'GitHub_Report_{target_year}_{month_range[0]:02d}_{month_range[1]:02d}.xlsx',
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )





