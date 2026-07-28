from __future__ import annotations

from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from src.config import AppSettings, ApplicationMode
from src.database import SQLiteReadOnlyDatabase
from src.models import TextToSQLRequest
from src.providers import (
    DemoTextToSQLProvider,
    LLMConfigurationError,
    LLMProviderError,
    ProviderType,
    create_provider,
)
from src.rbac import UserContext, UserRole
from src.service import TextToSQLService


PROVIDER_LABELS = {
    "OpenAI": ProviderType.OPENAI,
    "Gemini": ProviderType.GEMINI,
    "Claude": ProviderType.ANTHROPIC,
    "Ollama": ProviderType.OLLAMA,
}
PROVIDER_DISPLAY_NAMES = {
    ProviderType.DEMO: "Secure Demo",
    ProviderType.OPENAI: "OpenAI",
    ProviderType.GEMINI: "Gemini",
    ProviderType.ANTHROPIC: "Claude",
    ProviderType.OLLAMA: "Ollama",
}
ROLE_LABELS = {
    "Sales Analyst": UserRole.SALES_ANALYST,
    "Sales Manager": UserRole.SALES_MANAGER,
    "Account Manager": UserRole.ACCOUNT_MANAGER,
}
ROLE_DISPLAY_NAMES = {
    UserRole.SALES_ANALYST: "Sales Analyst",
    UserRole.SALES_MANAGER: "Sales Manager",
    UserRole.ACCOUNT_MANAGER: "Account Manager",
}
ROLE_PERMISSION_SUMMARIES = {
    UserRole.SALES_ANALYST: "Sales tables, restricted columns",
    UserRole.SALES_MANAGER: "All sales and employee data",
    UserRole.ACCOUNT_MANAGER: "Assigned customers and related orders only",
}
ERROR_TITLES = {
    "configuration_error": "Configuration Error",
    "provider_error": "Provider Error",
    "invalid_provider_response": "Invalid Provider Response",
    "unsafe_sql": "Unsafe SQL",
    "access_denied": "Access Denied",
    "database_error": "Database Error",
    "validation_error": "Validation Error",
}
EXAMPLE_QUESTIONS = {
    UserRole.SALES_ANALYST: [
        "What are the top 5 products by total completed-order revenue?",
        "Show monthly completed-order revenue for 2025.",
        "Which regions have the highest number of completed orders?",
        "Which customers placed the most completed orders?",
    ],
    UserRole.SALES_MANAGER: [
        "Which employees manage the highest-revenue customer portfolios?",
        "Show customer count by account manager.",
        "Compare completed-order revenue by region.",
        "Which products generate the most revenue?",
    ],
    UserRole.ACCOUNT_MANAGER: [
        "Show my assigned customers.",
        "Which of my customers generated the most revenue?",
        "Show completed orders for my customers.",
        "What products were purchased most by my customers?",
    ],
}
QUESTION_STATE_KEY = "question_text"
DEMO_QUESTION_STATE_KEY = "selected_demo_question"
ACTION_STATUS_SLOT_HEIGHT_PX = 16
LOCAL_FULL_SECURITY_NOTICE = (
    "Local full mode supports external providers. API keys are used only to construct "
    "the selected provider for the current request and are not written to project files."
)


@st.cache_resource
def load_settings() -> AppSettings:
    return AppSettings.from_env()


@st.cache_resource
def load_database(database_path: str, max_result_rows: int) -> SQLiteReadOnlyDatabase:
    return SQLiteReadOnlyDatabase(database_path=Path(database_path), max_result_rows=max_result_rows)


def main() -> None:
    st.set_page_config(
        page_title="Text-to-SQL with Guardrails",
        page_icon=":material/security:",
        layout="wide",
    )
    apply_compact_spacing()

    try:
        settings = load_settings()
        database = load_database(str(settings.database_path), settings.max_result_rows)
    except ValueError as exc:
        st.error(str(exc))
        return
    except FileNotFoundError:
        st.error("Sample database was not found. Run `py scripts/create_sample_db.py` first.")
        return

    render_app_header(settings)
    provider_type, model_name, api_key, ollama_host = render_provider_sidebar(settings)
    user = render_user_sidebar()
    if settings.is_public_demo:
        synchronize_demo_question_state(user.role)
    render_database_sidebar(database, settings, user)

    st.header("Ask the Database")
    question = render_question_input(settings.app_mode, user.role)

    validation_message = get_ui_validation_message(
        question,
        provider_type,
        model_name,
        api_key,
        user,
        settings.app_mode,
    )
    render_action_status_slot(settings.app_mode, validation_message)

    submitted = st.button(
        "Generate, Validate & Run",
        type="primary",
        disabled=validation_message is not None,
    )

    if submitted:
        try:
            provider = create_application_provider(
                settings=settings,
                provider_type=provider_type,
                model_name=model_name,
                api_key=api_key or None,
                ollama_host=ollama_host,
            )
            service = TextToSQLService(database=database, provider=provider)
            request = TextToSQLRequest(question=question, user=user)
        except ValueError as exc:
            st.error(str(exc))
            return
        except LLMProviderError:
            st.error("Provider configuration could not be initialized safely.")
            return

        with st.spinner("Generating, validating, authorizing, and executing SQL..."):
            response = service.answer(request)

        if response.success:
            try:
                render_success(response, settings.max_result_rows)
            except Exception:
                st.error("The response was generated, but the UI could not render it safely.")
        else:
            render_failure(response)


def render_provider_sidebar(settings: AppSettings) -> tuple[ProviderType, str, str, str]:
    if settings.is_public_demo:
        return ProviderType.DEMO, DemoTextToSQLProvider.model_name, "", settings.ollama_host

    st.sidebar.header("Model Configuration")
    st.sidebar.caption("Choose a provider and model. Cloud providers require your own API key.")
    default_label = next(
        label for label, provider_type in PROVIDER_LABELS.items() if provider_type == settings.default_provider
    )
    provider_label = st.sidebar.selectbox(
        "Provider",
        list(PROVIDER_LABELS),
        index=list(PROVIDER_LABELS).index(default_label),
    )
    provider_type = PROVIDER_LABELS[provider_label]

    api_key = ""
    ollama_host = settings.ollama_host
    model_defaults = {
        ProviderType.OPENAI: settings.openai_model,
        ProviderType.GEMINI: settings.gemini_model,
        ProviderType.ANTHROPIC: settings.anthropic_model,
        ProviderType.OLLAMA: settings.ollama_model,
    }
    model_name = st.sidebar.text_input("Model name", value=model_defaults[provider_type])

    if provider_type == ProviderType.OLLAMA:
        ollama_host = st.sidebar.text_input("Ollama host", value=settings.ollama_host)
        st.sidebar.caption("Ollama runs locally and does not require an API key.")
    else:
        api_key = st.sidebar.text_input("API key", type="password")

    return provider_type, model_name, api_key, ollama_host


def render_app_header(settings: AppSettings) -> None:
    st.title("Text-to-SQL with Guardrails")
    st.caption(
        "Ask structured business questions safely through SQL validation, role-based "
        "access control, and read-only execution."
    )
    st.markdown("`Question → SQL Generation → Guardrails → RBAC → Read-only Results`")
    if settings.is_public_demo:
        st.markdown("**Secure Public Demo · No API Key Required**")
        st.caption(
            "This hosted demo uses predefined business questions while the complete "
            "security pipeline remains active."
        )
    else:
        st.markdown("**Local Full Mode · External Providers Enabled**")
        st.caption(LOCAL_FULL_SECURITY_NOTICE)


def create_application_provider(
    settings: AppSettings,
    provider_type: ProviderType,
    model_name: str,
    api_key: str,
    ollama_host: str,
):
    if settings.is_public_demo:
        return DemoTextToSQLProvider()
    try:
        return create_provider(
            provider_type=provider_type,
            model_name=model_name,
            api_key=api_key or None,
            ollama_host=ollama_host,
        )
    except LLMProviderError:
        raise
    except Exception as exc:
        raise LLMConfigurationError("Selected provider could not be initialized.") from exc


def is_api_key_input_enabled(settings: AppSettings) -> bool:
    return settings.is_local_full


def render_user_sidebar() -> UserContext:
    st.sidebar.header("Access Control")
    role_label = st.sidebar.selectbox("Role", list(ROLE_LABELS))
    role = ROLE_LABELS[role_label]
    st.sidebar.caption(
        "Demo role selection. Backend RBAC controls the tables, columns, and rows available to each role."
    )
    st.sidebar.info(ROLE_PERMISSION_SUMMARIES[role])

    employee_id: int | None = None
    if role == UserRole.ACCOUNT_MANAGER:
        employee_id = int(st.sidebar.number_input("Account manager employee ID", min_value=1, step=1))
        user_id = f"demo_account_manager_{employee_id}"
    elif role == UserRole.SALES_MANAGER:
        user_id = "demo_sales_manager"
    else:
        user_id = "demo_sales_analyst"

    return UserContext(user_id=user_id, role=role, employee_id=employee_id)


def render_database_sidebar(
    database: SQLiteReadOnlyDatabase,
    settings: AppSettings,
    user: UserContext,
) -> None:
    st.sidebar.header("Database")
    st.sidebar.write(f"SQLite database: `{settings.database_path.name}`")
    st.sidebar.write(f"Max result rows: `{settings.max_result_rows}`")
    st.sidebar.write("Connection mode: `Read-only`")
    st.sidebar.write(f"Active role: `{get_role_display_name(user.role)}`")
    with st.sidebar.expander("View role-filtered schema"):
        st.code(database.get_schema_for_user(user), language="text")


def render_question_input(app_mode: ApplicationMode, role: UserRole) -> str:
    st.caption(
        "Select a business question to see how the system generates SQL, validates "
        "it, applies access controls, and executes it safely."
    )
    st.subheader("Suggested Questions")

    if app_mode == ApplicationMode.PUBLIC_DEMO:
        st.caption(
            "The hosted demo supports the questions below. Open-ended questions are "
            "available in Local Full Mode."
        )
        render_question_buttons(
            role=role,
            state_key=DEMO_QUESTION_STATE_KEY,
            key_prefix="demo_question",
            column_count=get_question_column_count(app_mode),
            equal_height=True,
        )
        selected_question = st.session_state.get(DEMO_QUESTION_STATE_KEY, "")
        selected_display = escape(get_selected_demo_question_display(selected_question))
        with st.container(key="question_input_area"):
            st.markdown(
                '<div class="selected-question-section">'
                '<div class="selected-question-label">Selected Question</div>'
                f'<div class="selected-question-panel" role="status">{selected_display}</div>'
                '<div class="question-mode-note-slot">'
                "Need open-ended questions? Run the project locally in Local Full Mode "
                "with OpenAI, Gemini, Claude, or Ollama."
                "</div></div>",
                unsafe_allow_html=True,
            )
        return selected_question

    st.caption("Choose a suggestion or enter an open-ended business question below.")
    render_question_buttons(
        role=role,
        state_key=QUESTION_STATE_KEY,
        key_prefix="local_question",
        column_count=get_question_column_count(app_mode),
        equal_height=True,
    )
    with st.container(key="question_input_area"):
        question = st.text_area(
            "Question",
            key=QUESTION_STATE_KEY,
            placeholder="Ask an open-ended question or choose a suggestion above",
            height=48,
        )
        st.markdown(
            '<div class="question-mode-note-slot" aria-hidden="true"></div>',
            unsafe_allow_html=True,
        )
    return question


def render_question_buttons(
    role: UserRole,
    state_key: str,
    key_prefix: str,
    column_count: int = 2,
    equal_height: bool = False,
) -> None:
    if equal_height:
        with st.container(key="question_button_grid"):
            _render_question_button_columns(role, state_key, key_prefix, column_count)
        return
    _render_question_button_columns(role, state_key, key_prefix, column_count)


def _render_question_button_columns(
    role: UserRole,
    state_key: str,
    key_prefix: str,
    column_count: int,
) -> None:
    columns = st.columns(column_count)
    for index, example in enumerate(EXAMPLE_QUESTIONS[role]):
        if columns[index % column_count].button(
            example,
            key=f"{key_prefix}_{role.value}_{index}",
            use_container_width=True,
        ):
            st.session_state.update(get_question_session_state_update(example, state_key))


def synchronize_demo_question_state(role: UserRole) -> None:
    selected_question = st.session_state.get(DEMO_QUESTION_STATE_KEY, "")
    if selected_question and not get_compatible_demo_question(selected_question, role):
        st.session_state.pop(DEMO_QUESTION_STATE_KEY, None)


def get_compatible_demo_question(question: str, role: UserRole) -> str:
    return question if question in EXAMPLE_QUESTIONS[role] else ""


def is_role_supported_demo_question(question: str, role: UserRole) -> bool:
    return bool(get_compatible_demo_question(question, role))


def uses_unrestricted_question_input(app_mode: ApplicationMode) -> bool:
    return app_mode == ApplicationMode.LOCAL_FULL


def get_sidebar_sections(app_mode: ApplicationMode) -> tuple[str, ...]:
    if app_mode == ApplicationMode.PUBLIC_DEMO:
        return ("Access Control", "Database")
    return ("Model Configuration", "Access Control", "Database")


def get_provider_control_names(app_mode: ApplicationMode) -> tuple[str, ...]:
    if app_mode == ApplicationMode.PUBLIC_DEMO:
        return ()
    return ("Provider", "Model name", "API key", "Ollama host")


def get_question_column_count(app_mode: ApplicationMode) -> int:
    return 4


def get_selected_demo_question_display(question: str) -> str:
    return question or "Select one of the questions above."


def get_action_status_text(
    app_mode: ApplicationMode,
    validation_message: str | None,
) -> str:
    if app_mode == ApplicationMode.LOCAL_FULL and validation_message:
        return validation_message
    return ""


def render_action_status_slot(
    app_mode: ApplicationMode,
    validation_message: str | None,
) -> None:
    status_text = escape(get_action_status_text(app_mode, validation_message))
    slot_class = "action-status-slot"
    if app_mode == ApplicationMode.LOCAL_FULL:
        slot_class += " local-action-status-slot"
    st.markdown(
        f'<div class="{slot_class}" style="height: '
        f'{ACTION_STATUS_SLOT_HEIGHT_PX}px">{status_text}</div>',
        unsafe_allow_html=True,
    )


def get_ui_validation_message(
    question: str,
    provider_type: ProviderType,
    model_name: str,
    api_key: str,
    user: UserContext,
    app_mode: ApplicationMode = ApplicationMode.LOCAL_FULL,
) -> str | None:
    if app_mode == ApplicationMode.PUBLIC_DEMO:
        if not question.strip():
            return "Choose a demo question to continue."
        if not is_role_supported_demo_question(question, user.role):
            return "Choose one of the demo questions available for the selected role."
        return None
    if not question.strip():
        return "Enter a question to continue."
    if not model_name.strip():
        return "Enter a model name to continue."
    if provider_type != ProviderType.OLLAMA and not api_key.strip():
        return "Enter an API key for the selected cloud provider."
    if provider_type == ProviderType.OLLAMA and not user.user_id:
        return "Select a valid user role."
    return None


def render_success(response, max_result_rows: int) -> None:
    render_status_flow()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Rows returned", response.row_count)
    col2.metric("Provider", get_provider_display_name(response.provider))
    col3.metric("Role", get_role_display_name(response.role))
    col4.metric("Result truncated", "Yes" if response.truncated else "No")

    with st.container(border=True):
        st.subheader("Query Explanation")
        st.write(response.explanation)

    st.subheader("Results")
    if response.rows:
        results_frame = pd.DataFrame(response.rows)
        st.dataframe(results_frame, use_container_width=True, hide_index=True)
        st.download_button(
            "Download CSV",
            data=create_results_csv(response.rows),
            file_name="query_results.csv",
            mime="text/csv",
        )
    else:
        st.info("The authorized query returned no rows.")

    rls_notice = get_row_level_security_notice(response)
    if rls_notice:
        st.info(rls_notice)

    generated_tab, authorized_tab = st.tabs(["Generated SQL", "Authorized SQL"])
    with generated_tab:
        st.caption("SQL returned by the selected provider before backend authorization.")
        st.code(response.generated_sql, language="sql")
    with authorized_tab:
        st.caption("SQL approved for read-only execution after guardrails and RBAC.")
        st.code(response.authorized_sql, language="sql")

    with st.expander("Security & Access Details"):
        st.markdown("**SQL Guardrails**")
        for check in response.guardrail_checks:
            st.markdown(f"- [OK] {check}")
        st.markdown("**RBAC**")
        for check in response.rbac_checks:
            st.markdown(f"- [OK] {check}")
        st.markdown("**Execution Controls**")
        st.markdown("- [OK] Read-only SQLite connection")
        st.markdown("- [OK] PRAGMA query_only enabled")
        st.markdown(f"- [OK] Result limit: {max_result_rows}")
        st.markdown("- [OK] Role-filtered schema sent to the LLM")
        st.markdown("- [OK] Generated SQL was not executed directly")


def render_status_flow() -> None:
    st.success("SQL Generated -> Guardrails Passed -> RBAC Enforced -> Read-only Executed")


def render_failure(response) -> None:
    title = ERROR_TITLES.get(response.error_type or "", "Request Failed")
    st.error(f"{title}: {response.error_message}")
    if response.generated_sql:
        with st.expander("Rejected SQL"):
            st.code(response.generated_sql, language="sql")


def get_provider_display_name(provider: ProviderType | str) -> str:
    if isinstance(provider, ProviderType):
        return PROVIDER_DISPLAY_NAMES[provider]
    try:
        return PROVIDER_DISPLAY_NAMES[ProviderType(provider)]
    except ValueError:
        return provider.title()


def get_role_display_name(role: UserRole | str) -> str:
    if isinstance(role, UserRole):
        return ROLE_DISPLAY_NAMES[role]
    try:
        return ROLE_DISPLAY_NAMES[UserRole(role)]
    except ValueError:
        return role.replace("_", " ").title()


def get_example_session_state_update(question: str) -> dict[str, str]:
    return get_question_session_state_update(question, QUESTION_STATE_KEY)


def get_demo_question_session_state_update(question: str) -> dict[str, str]:
    return get_question_session_state_update(question, DEMO_QUESTION_STATE_KEY)


def get_question_session_state_update(question: str, state_key: str) -> dict[str, str]:
    return {state_key: question}


def create_results_csv(rows: list[dict[str, object]]) -> str:
    return pd.DataFrame(rows).to_csv(index=False)


def should_show_row_level_security_notice(response) -> bool:
    return bool(response.row_level_security_applied)


def get_row_level_security_notice(response) -> str | None:
    if not should_show_row_level_security_notice(response):
        return None
    return "Row-level security was applied. Review the Authorized SQL tab to see the enforced data restrictions."


def apply_compact_spacing() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.4rem;
        }
        div[data-testid="stVerticalBlock"] {
            gap: 0.65rem;
        }
        .selected-question-section {
            margin-top: 0;
        }
        .selected-question-label {
            margin-bottom: 0.3rem;
            font-size: 0.95rem;
            font-weight: 600;
        }
        .selected-question-panel {
            display: flex;
            align-items: center;
            height: 48px;
            min-height: 48px;
            padding: 0.35rem 0.7rem;
            box-sizing: border-box;
            border: 1px solid rgba(128, 128, 128, 0.35);
            border-radius: 0.5rem;
            background: rgba(128, 128, 128, 0.08);
            font-size: 0.9rem;
            line-height: 1.2;
            overflow: hidden;
        }
        .question-mode-note-slot {
            height: 17px;
            margin-top: 5px;
            font-size: 0.875rem;
            line-height: 17px;
            opacity: 0.7;
            overflow: hidden;
        }
        .st-key-question_input_area div[data-testid="stTextArea"] textarea {
            height: 48px;
            min-height: 48px;
            padding: 0.35rem 0.7rem;
            border-radius: 0.5rem;
            font-size: 0.9rem;
            line-height: 1.2;
            resize: none;
        }
        .st-key-question_input_area div[data-testid="stVerticalBlock"] {
            gap: 0;
        }
        .action-status-slot {
            font-size: 0.8rem;
            line-height: 16px;
            opacity: 0.7;
            overflow: hidden;
        }
        .local-action-status-slot {
            margin-bottom: 7px;
        }
        .st-key-question_button_grid div[data-testid="stButton"] button {
            height: 48px;
            min-height: 48px;
            padding: 0.25rem 0.5rem;
            border: 1px solid rgba(128, 128, 128, 0.35);
            border-radius: 0.5rem;
            white-space: normal;
            transition: background-color 120ms ease, border-color 120ms ease;
        }
        .st-key-question_button_grid div[data-testid="stButton"] button p {
            display: -webkit-box;
            margin: 0;
            overflow: hidden;
            -webkit-box-orient: vertical;
            -webkit-line-clamp: 2;
            font-size: 0.875rem;
            line-height: 1.15;
            text-align: center;
        }
        .st-key-question_button_grid div[data-testid="stButton"] button:hover {
            border-color: rgba(128, 128, 128, 0.65);
            background: rgba(128, 128, 128, 0.10);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
