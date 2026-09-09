# ---------------------------------------------------------------------------
# Tarpeyo Treatment Pathway dashboard
#
# rocker/r-ver is configured to pull precompiled Linux binaries from Posit
# Public Package Manager, so this builds in a few minutes rather than the
# 30+ it would take compiling plotly's dependency tree from source.
#
# The pinned snapshot date is what makes the build reproducible. Bump it
# deliberately, not accidentally.
# ---------------------------------------------------------------------------
FROM rocker/r-ver:4.4.1

ENV DEBIAN_FRONTEND=noninteractive

# httr/curl need libcurl and libssl. curl(1) is used by the ECS health check.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libcurl4-openssl-dev \
        libssl-dev \
        libxml2-dev \
    && rm -rf /var/lib/apt/lists/*

# Pin to a dated snapshot so rebuilds get identical package versions.
ENV CRAN_SNAPSHOT=https://packagemanager.posit.co/cran/__linux__/jammy/2026-05-01

RUN R -q -e "options(repos = c(CRAN = Sys.getenv('CRAN_SNAPSHOT'))); \
      install.packages(c( \
        'shiny', \
        'dplyr', \
        'tidyr', \
        'stringr', \
        'plotly', \
        'scales', \
        'readxl' \
      )); \
      if (!all(c('shiny','dplyr','tidyr','stringr','plotly','scales','readxl') \
               %in% rownames(installed.packages()))) quit(status = 1)"

WORKDIR /srv/app

# app.R reads the spreadsheet from the working directory at startup, so it is
# baked into the image. It is 20 KB; there is no reason to put it in S3.
COPY app.R .
COPY Tx_sequence_after_2022_tarpeyo.xlsx .

EXPOSE 3838

# Shiny is served directly rather than through Shiny Server. One app, one
# process, one container: the extra layer would only add image size and a
# config file to get wrong.
CMD ["R", "-q", "-e", "shiny::runApp('/srv/app', host = '0.0.0.0', port = 3838, launch.browser = FALSE)"]
