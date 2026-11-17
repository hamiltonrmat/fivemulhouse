import hmac
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

# --- CONFIGURATION ---
st.set_page_config(page_title="Futsal - Inscriptions", page_icon="⚽", layout="centered")

PARIS_TZ = ZoneInfo("Europe/Paris")
MAX_TITULAIRES = 10
MAX_TOTAL = 25

SEANCES_HEADERS = ["id_seance", "date_seance", "titre", "statut", "created_at"]
INSCRIPTIONS_HEADERS = [
    "id_inscription",
    "id_seance",
    "nom",
    "timestamp_iso",
    "heure_affichage",
    "statut",
]


# --- CONNEXION GOOGLE SHEETS ---
@st.cache_resource
def get_spreadsheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_info = dict(st.secrets["gsheets"])
    spreadsheet_url = creds_info.pop("spreadsheet_url")

    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    client = gspread.authorize(creds)
    sh = client.open_by_url(spreadsheet_url)

    # Création automatique des onglets et en-têtes s'ils n'existent pas
    for title, headers in [
        ("seances", SEANCES_HEADERS),
        ("inscriptions", INSCRIPTIONS_HEADERS),
    ]:
        try:
            ws = sh.worksheet(title)
            if not ws.row_values(1):
                ws.append_row(headers, value_input_option="RAW")
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers))
            ws.append_row(headers, value_input_option="RAW")

    return sh


@st.cache_data(ttl=3)
def load_seances() -> pd.DataFrame:
    ws = get_spreadsheet().worksheet("seances")
    records = ws.get_all_records(numericise_ignore=["all"])
    if not records:
        return pd.DataFrame(columns=SEANCES_HEADERS)
    return pd.DataFrame(records)


@st.cache_data(ttl=3)
def load_inscriptions() -> pd.DataFrame:
    ws = get_spreadsheet().worksheet("inscriptions")
    records = ws.get_all_records(numericise_ignore=["all"])
    if not records:
        return pd.DataFrame(columns=INSCRIPTIONS_HEADERS)
    return pd.DataFrame(records)


def get_active_inscriptions(seance_id: str) -> pd.DataFrame:
    df = load_inscriptions()
    if df.empty:
        return df
    actifs = df[(df["id_seance"] == seance_id) & (df["statut"] == "INSCRIT")].copy()
    if actifs.empty:
        return actifs
    actifs["dt_sort"] = pd.to_datetime(actifs["timestamp_iso"])
    actifs = actifs.sort_values("dt_sort", ascending=True).reset_index(drop=True)
    return actifs


def set_inscription_annulee(inscription_id: str) -> bool:
    ws = get_spreadsheet().worksheet("inscriptions")
    cell = ws.find(inscription_id, in_column=1)
    if cell is None:
        return False
    ws.update_cell(cell.row, 6, "ANNULE")  # Colonne 6 = statut
    return True


def close_seance_in_sheet(seance_id: str) -> bool:
    ws = get_spreadsheet().worksheet("seances")
    cell = ws.find(seance_id, in_column=1)
    if cell is None:
        return False
    ws.update_cell(cell.row, 4, "FERMEE")  # Colonne 4 = statut
    return True


def clear_all_caches():
    load_seances.clear()
    load_inscriptions.clear()


# --- GESTION DES MOTS DE PASSE ---
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "is_admin" not in st.session_state:
    st.session_state.is_admin = False
if "flash_msg" not in st.session_state:
    st.session_state.flash_msg = None


def check_app_password():
    st.title("⚽ Futsal — Accès privé")
    with st.form("login_form"):
        pwd = st.text_input("Mot de passe du groupe", type="password")
        submit = st.form_submit_button("Entrer", width="stretch")
        if submit:
            expected = st.secrets["passwords"]["app_password"]
            if hmac.compare_digest(pwd, expected):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Mot de passe incorrect.")


if not st.session_state.authenticated:
    check_app_password()
    st.stop()


# --- INTERFACE PRINCIPALE ---
col_title, col_refresh = st.columns([4, 1])
with col_title:
    st.title("⚽ Futsal — Inscriptions")
with col_refresh:
    st.write("")
    if st.button("🔄 Actualiser", width="stretch"):
        clear_all_caches()
        st.rerun()

if st.session_state.flash_msg:
    kind, msg = st.session_state.flash_msg
    st.session_state.flash_msg = None
    if kind == "success":
        st.success(msg)
    elif kind == "warning":
        st.warning(msg)
    elif kind == "error":
        st.error(msg)

tab_inscriptions, tab_admin = st.tabs(["📋 Inscriptions", "🔐 Administration"])

# ==========================================
# ONGLET 1 : INSCRIPTIONS DES JOUEURS
# ==========================================
with tab_inscriptions:
    df_seances = load_seances()
    seances_ouvertes = (
        df_seances[df_seances["statut"] == "OUVERTE"].iloc[::-1]
        if not df_seances.empty
        else pd.DataFrame()
    )

    if seances_ouvertes.empty:
        st.info(
            "⏳ Aucune séance n'est ouverte aux inscriptions pour le moment.\n\n"
            "Un administrateur doit d'abord créer la prochaine séance dans l'onglet **Administration**."
        )
    else:
        # Sélection de la séance (s'il y en a plusieurs ouvertes)
        options_seances = {
            row["id_seance"]: f"📅 {row['date_seance']} — {row['titre']}"
            for _, row in seances_ouvertes.iterrows()
        }
        if len(options_seances) == 1:
            selected_seance_id = list(options_seances.keys())[0]
            st.subheader(options_seances[selected_seance_id])
        else:
            selected_seance_id = st.selectbox(
                "Séance :",
                options=list(options_seances.keys()),
                format_func=lambda k: options_seances[k],
            )

        actifs = get_active_inscriptions(selected_seance_id)
        nb_total = len(actifs)
        nb_titulaires = min(nb_total, MAX_TITULAIRES)
        nb_attente = max(0, min(nb_total, MAX_TOTAL) - MAX_TITULAIRES)

        c1, c2 = st.columns(2)
        c1.metric("🟢 Titulaires", f"{nb_titulaires} / {MAX_TITULAIRES}")
        c2.metric(
            "🟡 Liste d'attente",
            f"{nb_attente} / {MAX_TOTAL - MAX_TITULAIRES}",
        )

        # --- FORMULAIRE D'INSCRIPTION ---
        st.divider()
        if nb_total >= MAX_TOTAL:
            st.warning(
                f"⛔ La séance est complète ({MAX_TOTAL}/{MAX_TOTAL}, liste d'attente incluse). "
                "Surveillez s'il y a un désistement !"
            )
        else:
            with st.form("form_inscription", clear_on_submit=True):
                nom_saisi = st.text_input(
                    "Votre nom / prénom",
                    placeholder="Ex : Lucas M.",
                    max_chars=50,
                )
                btn_inscrire = st.form_submit_button(
                    "✅ M'inscrire maintenant", width="stretch"
                )

                if btn_inscrire:
                    nom_clean = " ".join(nom_saisi.strip().split())
                    if not nom_clean:
                        st.error("Merci d'entrer votre nom avant de valider.")
                    else:
                        # Relecture fraîche avant écriture pour limiter les conflits
                        clear_all_caches()
                        actifs_fresh = get_active_inscriptions(selected_seance_id)
                        noms_existants = {
                            n.lower() for n in actifs_fresh.get("nom", [])
                        }

                        if nom_clean.lower() in noms_existants:
                            st.error(f"« {nom_clean} » est déjà inscrit sur cette séance.")
                        elif len(actifs_fresh) >= MAX_TOTAL:
                            st.error("Désolé, la 25e place vient tout juste d'être prise !")
                        else:
                            now = datetime.now(PARIS_TZ)
                            insc_id = uuid.uuid4().hex[:12]
                            ws_insc = get_spreadsheet().worksheet("inscriptions")
                            ws_insc.append_row(
                                [
                                    insc_id,
                                    selected_seance_id,
                                    nom_clean,
                                    now.isoformat(),
                                    now.strftime("%H:%M:%S"),
                                    "INSCRIT",
                                ],
                                value_input_option="RAW",
                            )

                            # Vérification post-écriture en cas de clics à la même milliseconde sur la 25e place
                            clear_all_caches()
                            actifs_after = get_active_inscriptions(selected_seance_id)
                            rang_list = actifs_after.index[
                                actifs_after["id_inscription"] == insc_id
                            ].tolist()

                            if rang_list and rang_list[0] >= MAX_TOTAL:
                                set_inscription_annulee(insc_id)
                                clear_all_caches()
                                st.error(
                                    "Désolé, la 25e place a été prise à la même fraction de seconde !"
                                )
                            else:
                                pos = (rang_list[0] + 1) if rang_list else len(actifs_after)
                                if pos <= MAX_TITULAIRES:
                                    msg = f"🎉 {nom_clean}, tu es inscrit en **Titulaire #{pos}** ({now.strftime('%H:%M:%S')}) !"
                                else:
                                    msg = f"🟡 {nom_clean}, tu es en **Liste d'attente #{pos - MAX_TITULAIRES}** ({now.strftime('%H:%M:%S')})."
                                st.session_state.flash_msg = ("success", msg)
                                st.rerun()

        # --- AFFICHAGE DES LISTES ---
        st.divider()
        st.subheader("🟢 Titulaires (1 à 10)")
        titulaires_df = actifs.iloc[:MAX_TITULAIRES].copy()
        if titulaires_df.empty:
            st.caption("Aucun joueur inscrit pour le moment.")
        else:
            titulaires_df.insert(0, "Place", range(1, len(titulaires_df) + 1))
            st.dataframe(
                titulaires_df[["Place", "nom", "heure_affichage"]].rename(
                    columns={"nom": "Joueur", "heure_affichage": "Heure d'inscription"}
                ),
                hide_index=True,
                width="stretch",
            )

        st.subheader("🟡 Liste d'attente (11 à 25)")
        attente_df = actifs.iloc[MAX_TITULAIRES:MAX_TOTAL].copy()
        if attente_df.empty:
            st.caption("Personne en liste d'attente.")
        else:
            attente_df.insert(0, "Rang attente", [f"#{i}" for i in range(1, len(attente_df) + 1)])
            st.dataframe(
                attente_df[["Rang attente", "nom", "heure_affichage"]].rename(
                    columns={"nom": "Joueur", "heure_affichage": "Heure d'inscription"}
                ),
                hide_index=True,
                width="stretch",
            )

        # --- DÉSISTEMENT ---
        if not actifs.empty:
            st.divider()
            with st.expander("🚪 Se désinscrire de la séance"):
                choix_joueurs = {
                    row["id_inscription"]: (
                        f"#{idx + 1} — {row['nom']} "
                        f"({'Titulaire' if idx < MAX_TITULAIRES else f'Attente #{idx - MAX_TITULAIRES + 1}'})"
                    )
                    for idx, row in actifs.iterrows()
                }
                id_a_annuler = st.selectbox(
                    "Sélectionnez votre nom :",
                    options=list(choix_joueurs.keys()),
                    format_func=lambda k: choix_joueurs[k],
                )
                confirmer = st.checkbox("Je confirme vouloir libérer ma place")
                if st.button("❌ Confirmer ma désinscription", disabled=not confirmer):
                    set_inscription_annulee(id_a_annuler)
                    clear_all_caches()
                    st.session_state.flash_msg = (
                        "warning",
                        "Désinscription prise en compte. La liste a été mise à jour automatiquement !",
                    )
                    st.rerun()

# ==========================================
# ONGLET 2 : ADMINISTRATION
# ==========================================
with tab_admin:
    if not st.session_state.is_admin:
        st.subheader("Connexion Administrateur")
        with st.form("admin_login"):
            admin_pwd = st.text_input("Mot de passe admin", type="password")
            if st.form_submit_button("Déverrouiller"):
                expected_admin = st.secrets["passwords"]["admin_password"]
                if hmac.compare_digest(admin_pwd, expected_admin):
                    st.session_state.is_admin = True
                    st.rerun()
                else:
                    st.error("Mot de passe administrateur incorrect.")
    else:
        st.success("🔓 Mode administrateur activé")

        st.subheader("➕ Créer une nouvelle séance")
        with st.form("form_create_seance"):
            date_seance = st.date_input(
                "Date de la séance",
                value=datetime.now(PARIS_TZ).date(),
                format="DD/MM/YYYY",
            )
            titre_seance = st.text_input(
                "Créneau / Lieu (optionnel)",
                placeholder="Ex : 20h00 - 21h00 · UrbanSoccer",
            )
            fermer_precedentes = st.checkbox(
                "Clôturer automatiquement les séances encore ouvertes", value=True
            )
            if st.form_submit_button("🆕 Créer et ouvrir la séance", width="stretch"):
                clear_all_caches()
                df_s = load_seances()

                if fermer_precedentes and not df_s.empty:
                    for _, r in df_s[df_s["statut"] == "OUVERTE"].iterrows():
                        close_seance_in_sheet(r["id_seance"])

                now = datetime.now(PARIS_TZ)
                new_id = uuid.uuid4().hex[:10]
                ws_seances = get_spreadsheet().worksheet("seances")
                ws_seances.append_row(
                    [
                        new_id,
                        date_seance.strftime("%d/%m/%Y"),
                        titre_seance.strip() or "Séance Futsal",
                        "OUVERTE",
                        now.strftime("%d/%m/%Y %H:%M:%S"),
                    ],
                    value_input_option="RAW",
                )
                clear_all_caches()
                st.session_state.flash_msg = (
                    "success",
                    f"Séance du {date_seance.strftime('%d/%m/%Y')} créée ! Elle démarre vide : pensez à vous inscrire dans l'onglet Inscriptions.",
                )
                st.rerun()

        st.divider()
        st.subheader("📋 Séances existantes")
        df_s = load_seances()
        if df_s.empty:
            st.caption("Aucune séance créée.")
        else:
            ouvertes = df_s[df_s["statut"] == "OUVERTE"]
            if not ouvertes.empty:
                for _, row in ouvertes.iterrows():
                    c_info, c_btn = st.columns([3, 1])
                    c_info.write(f"🟢 **{row['date_seance']}** — {row['titre']}")
                    if c_btn.button("🔒 Clôturer", key=f"close_{row['id_seance']}"):
                        close_seance_in_sheet(row["id_seance"])
                        clear_all_caches()
                        st.rerun()

            st.dataframe(df_s.iloc[::-1], hide_index=True, width="stretch")
