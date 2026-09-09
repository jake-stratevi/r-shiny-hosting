# ---------------------------------------------------------------------------
# Access control for ALB + Cognito hosted Shiny apps.
#
# The ALB authenticates before any request reaches this container, then injects
# the caller's identity as headers. This module reads them and decides whether
# the caller may see this particular app — which the ALB itself cannot do,
# because its authenticate-cognito action is binary.
#
# Driven by ACCESS_MODE:
#
#   off      No check. The default, so `docker run` locally still works.
#   emails   Allow if the caller's email is in ALLOWED_EMAILS (comma separated).
#   groups   Allow if any of the caller's Cognito groups is in ALLOWED_GROUPS.
#
# The two enforcing modes exist so moving from per-app email lists to Cognito
# groups is an environment variable change, not a code change. Start on
# `emails`; switch to `groups` once groups are populated and synced from Entra.
#
# SECURITY NOTE — the JWT signature is not verified here.
#
# x-amzn-oidc-data is signed by the ALB with ES256, and the correct hardening is
# to fetch the ALB's public key from
# https://public-keys.auth.elb.<region>.amazonaws.com/<kid> and verify. We do
# not, because the task's security group accepts traffic only from the ALB's
# security group, so there is no path by which a forged header can arrive.
#
# That reasoning stops holding the moment the task becomes reachable by anything
# else — a second ingress, a service mesh, a debugging tunnel. If that changes,
# verify the signature.
# ---------------------------------------------------------------------------

# --- header parsing --------------------------------------------------------

.b64url_decode <- function(x) {
  x <- gsub("-", "+", x, fixed = TRUE)
  x <- gsub("_", "/", x, fixed = TRUE)
  pad <- nchar(x) %% 4
  if (pad > 0) x <- paste0(x, strrep("=", 4 - pad))
  rawToChar(jsonlite::base64_dec(x))
}

.jwt_claims <- function(token) {
  if (is.null(token) || !nzchar(token)) return(list())
  parts <- strsplit(token, ".", fixed = TRUE)[[1]]
  if (length(parts) < 2) return(list())
  tryCatch(
    jsonlite::fromJSON(.b64url_decode(parts[2]), simplifyVector = FALSE),
    error = function(e) list()
  )
}

#' Build a principal from an ALB-authenticated request.
#'
#' Returns a list with email, sub, name and groups. Every field may be empty —
#' callers must not assume an email is present.
access_principal <- function(req) {
  oidc_data <- req$HTTP_X_AMZN_OIDC_DATA
  identity  <- req$HTTP_X_AMZN_OIDC_IDENTITY

  claims <- .jwt_claims(oidc_data)

  # A federated Cognito user often arrives with no `email` claim and a
  # synthetic username like "microsoft365_gwww7jtfv4cb0km6n1y5qtda...". Walk
  # the claims Entra actually populates, in order of how much they mean to a
  # person, and only accept a value that looks like an address.
  email <- ""
  for (key in c("email", "upn", "preferred_username", "custom:email")) {
    value <- tolower(trimws(as.character(claims[[key]] %||% "")))
    if (grepl("@", value, fixed = TRUE)) {
      email <- value
      break
    }
  }

  # Groups live in the ACCESS token, not in x-amzn-oidc-data. The ALB builds
  # oidc-data from Cognito's userinfo endpoint, which returns standard OIDC
  # claims only and omits cognito:groups entirely.
  access_claims <- .jwt_claims(req$HTTP_X_AMZN_OIDC_ACCESSTOKEN)
  groups <- unlist(access_claims[["cognito:groups"]] %||% list())

  list(
    email  = email,
    sub    = as.character(identity %||% claims[["sub"]] %||% ""),
    name   = as.character(claims[["name"]] %||% ""),
    groups = as.character(groups),
    authenticated = nzchar(as.character(identity %||% "")) ||
                    length(claims) > 0
  )
}

`%||%` <- function(a, b) if (is.null(a)) b else a

.env_list <- function(name) {
  raw <- Sys.getenv(name, unset = "")
  if (!nzchar(raw)) return(character(0))
  tolower(trimws(strsplit(raw, ",", fixed = TRUE)[[1]]))
}

# --- the decision ----------------------------------------------------------

#' TRUE if this principal may use this app.
access_allowed <- function(principal) {
  mode <- tolower(Sys.getenv("ACCESS_MODE", unset = "off"))

  if (identical(mode, "off")) return(TRUE)

  # Fail closed. An unauthenticated request reaching an enforcing app means
  # something is wrong with the ALB rule, and the safe response is refusal.
  if (!isTRUE(principal$authenticated)) return(FALSE)

  if (identical(mode, "emails")) {
    allowed <- .env_list("ALLOWED_EMAILS")
    return(nzchar(principal$email) && principal$email %in% allowed)
  }

  if (identical(mode, "groups")) {
    allowed <- .env_list("ALLOWED_GROUPS")
    return(any(tolower(principal$groups) %in% allowed))
  }

  # Unrecognised mode is a configuration error, not a reason to let people in.
  FALSE
}

# --- what a refused user sees ----------------------------------------------

access_denied_page <- function(principal, app_label = NULL) {
  app_label <- app_label %||% Sys.getenv("APP_LABEL", unset = "this application")
  contact   <- Sys.getenv("ACCESS_CONTACT", unset = "your Stratevi contact")
  logout    <- Sys.getenv("LOGOUT_URL", unset = "")

  # Never show the synthetic id where a name belongs — it looks broken and
  # tells the user nothing. Say the account is unmapped instead.
  who <- if (nzchar(principal$email)) {
    principal$email
  } else if (nzchar(principal$name)) {
    principal$name
  } else {
    "an account with no email address"
  }

  shiny::tags$html(
    shiny::tags$head(
      shiny::tags$title("No access"),
      shiny::tags$style(shiny::HTML("
        body { font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
               background: #f7f8fa; color: #1c2230; display: flex;
               align-items: center; justify-content: center; height: 100vh; margin: 0; }
        .card { background: #fff; border: 1px solid #e2e6ee; border-radius: 10px;
                padding: 40px 48px; max-width: 520px;
                box-shadow: 0 1px 3px rgba(20,30,60,.06); }
        h1 { font-size: 19px; margin: 0 0 14px; font-weight: 600; }
        p  { font-size: 14px; line-height: 1.6; color: #55607a; margin: 0 0 12px; }
        code { background: #f1f3f8; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
        a { color: #3b6ce4; }
      "))
    ),
    shiny::tags$body(
      shiny::tags$div(
        class = "card",
        shiny::tags$h1("You don't have access to this dashboard"),
        shiny::tags$p(
          "You're signed in as ", shiny::tags$code(who),
          ", but that account isn't on the access list for ", app_label, "."
        ),
        shiny::tags$p(
          "If you think this is wrong, contact ", contact,
          " and mention the address above."
        ),
        if (nzchar(logout)) {
          shiny::tags$p(shiny::tags$a(href = logout, "Sign in as someone else"))
        }
      )
    )
  )
}

#' Wrap an app's UI in an access check.
#'
#' Refusal happens at page load, before any server logic runs, so an
#' unauthorised user never reaches reactive code or the underlying data.
#'
#' @param main_ui The UI object to serve when access is granted.
access_gate <- function(main_ui) {
  function(req) {
    principal <- access_principal(req)
    if (access_allowed(principal)) main_ui else access_denied_page(principal)
  }
}
