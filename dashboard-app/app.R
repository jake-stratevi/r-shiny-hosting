


# ============================================================
# Interactive Tarpeyo Treatment Pathway Shiny App
#
# Features:
#   - Native Plotly Sankey graph with visible connectors
#   - Highlight pathways containing a selected treatment
#   - Restrict selection to a specific step
#   - Example: Tarpeyo at Step 4
#   - Hover over connectors to view pathway details
#   - Hover over nodes to view treatment and step
#
# Required dataset:
#   df_tarpeyo
#
# Required variables:
#   treatment_sequence_4steps
#   n_patients
#   tarpeyo_start_group
# ============================================================


# ============================================================
# Load packages
# ============================================================

library(shiny)
library(dplyr)
library(tidyr)
library(stringr)
library(plotly)
library(scales)
library(readxl)
library(jsonlite)   # used by access.R to decode the ALB identity headers

source("access.R")



df_tarpeyo <- read_excel('Tx_sequence_after_2022_tarpeyo.xlsx')


df_tarpeyo <- df_tarpeyo %>%
  mutate(
    tarpeyo_start_group = if_else(
      str_detect(str_trim(treatment_sequence_4steps), "^Tarpeyo"),
      "Started with Tarpeyo",
      "Did not start with Tarpeyo"
    )
  )
# ============================================================
# Check input dataset
# ============================================================

stopifnot(is.data.frame(df_tarpeyo))

required_vars <- c(
  "treatment_sequence_4steps",
  "n_patients",
  "tarpeyo_start_group"
)

missing_vars <- setdiff(
  required_vars,
  names(df_tarpeyo)
)

if (length(missing_vars) > 0) {
  stop(
    paste0(
      "The following variables are missing from df_tarpeyo: ",
      paste(missing_vars, collapse = ", ")
    )
  )
}


# Convert patient count to numeric if needed
df_tarpeyo <- df_tarpeyo %>%
  mutate(
    n_patients = as.numeric(n_patients)
  )


# ============================================================
# User interface
# ============================================================

main_ui <- fluidPage(

  # --- Scale-to-zero heartbeat ------------------------------------------
  # Required by the AWS deployment. Idle detection reads the ALB metric
  # RequestCountPerTarget; a Shiny websocket emits no HTTP requests, so an
  # active user would look idle and the sleeper Lambda would scale the task
  # to zero underneath them. One HEAD request per minute per open session
  # keeps the app visibly alive. Harmless when run locally.
  tags$head(tags$script(HTML("
    setInterval(function () {
      fetch(window.location.pathname + '?heartbeat=' + Date.now(),
            { method: 'HEAD', cache: 'no-store' });
    }, 60000);
  "))),

  titlePanel(
    "Tarpeyo Treatment Pathways"
  ),
  
  sidebarLayout(
    
    sidebarPanel(
      
      sliderInput(
        inputId = "top_n",
        label = "Number of pathways:",
        min = 5,
        max = max(
          5,
          min(100, nrow(df_tarpeyo))
        ),
        value = min(
          40,
          max(5, nrow(df_tarpeyo))
        ),
        step = 5
      ),
      
      radioButtons(
        inputId = "tarpeyo_group",
        label = "Tarpeyo initiation:",
        choices = c(
          "Tarpeyo started later" = "later",
          "Started with Tarpeyo" = "start",
          "All Tarpeyo pathways" = "all"
        ),
        selected = "later"
      ),
      
      selectizeInput(
        inputId = "selected_treatment",
        label = "Highlight treatment:",
        choices = NULL,
        selected = "",
        multiple = FALSE,
        options = list(
          placeholder = "Select a treatment category",
          allowEmptyOption = TRUE
        )
      ),
      
      selectInput(
        inputId = "selected_step",
        label = "At treatment step:",
        choices = c(
          "Any step" = "all",
          "Step 1" = "Step 1",
          "Step 2" = "Step 2",
          "Step 3" = "Step 3",
          "Step 4" = "Step 4"
        ),
        selected = "all"
      ),
      
      actionButton(
        inputId = "clear_selection",
        label = "Clear highlight"
      ),
      
      br(),
      br(),
      
      sliderInput(
        inputId = "plot_height",
        label = "Graph height:",
        min = 600,
        max = 1600,
        value = 900,
        step = 100
      ),
      
      sliderInput(
        inputId = "node_width",
        label = "Treatment-box width:",
        min = 10,
        max = 40,
        value = 20,
        step = 2
      ),
      
      sliderInput(
        inputId = "node_padding",
        label = "Space between treatment boxes:",
        min = 5,
        max = 40,
        value = 15,
        step = 2
      )
    ),
    
    mainPanel(
      
      uiOutput(
        outputId = "dynamic_plot"
      ),
      
      br(),
      
      h4(
        "Highlighted pathways"
      ),
      
      verbatimTextOutput(
        outputId = "selected_summary"
      )
    )
  )
)


# ============================================================
# Server
# ============================================================

server <- function(input, output, session) {
  
  
  # ----------------------------------------------------------
  # Dynamic graph height
  # ----------------------------------------------------------
  
  output$dynamic_plot <- renderUI({
    
    plotlyOutput(
      outputId = "sankey_plot",
      height = paste0(
        input$plot_height,
        "px"
      )
    )
  })
  
  
  # ----------------------------------------------------------
  # Filter and prepare pathway-level data
  # ----------------------------------------------------------
  
  plot_data <- reactive({
    
    dat <- df_tarpeyo %>%
      filter(
        !is.na(treatment_sequence_4steps),
        str_trim(treatment_sequence_4steps) != "",
        !is.na(n_patients),
        n_patients > 0
      )
    
    if (input$tarpeyo_group == "later") {
      
      dat <- dat %>%
        filter(
          is.na(tarpeyo_start_group) |
            tarpeyo_start_group != "Started with Tarpeyo"
        )
    }
    
    if (input$tarpeyo_group == "start") {
      
      dat <- dat %>%
        filter(
          tarpeyo_start_group == "Started with Tarpeyo"
        )
    }
    
    dat <- dat %>%
      group_by(
        treatment_sequence_4steps,
        tarpeyo_start_group
      ) %>%
      summarise(
        n_patients = sum(
          n_patients,
          na.rm = TRUE
        ),
        .groups = "drop"
      ) %>%
      arrange(
        desc(n_patients)
      ) %>%
      slice_head(
        n = input$top_n
      ) %>%
      mutate(
        sequence_id = row_number()
      ) %>%
      separate(
        col = treatment_sequence_4steps,
        into = c(
          "step1",
          "step2",
          "step3",
          "step4"
        ),
        sep = "\\s*->\\s*",
        fill = "right",
        extra = "drop",
        remove = FALSE
      ) %>%
      mutate(
        across(
          c(step1, step2, step3, step4),
          ~ na_if(str_trim(.x), "")
        )
      )
    
    dat
  })
  
  
  # ----------------------------------------------------------
  # Convert treatment steps to long format
  # ----------------------------------------------------------
  
  plot_long <- reactive({
    
    plot_data() %>%
      pivot_longer(
        cols = c(
          step1,
          step2,
          step3,
          step4
        ),
        names_to = "step_variable",
        values_to = "drug_category"
      ) %>%
      filter(
        !is.na(drug_category),
        str_trim(drug_category) != ""
      ) %>%
      mutate(
        drug_category = str_trim(
          drug_category
        ),
        
        step_number = case_when(
          step_variable == "step1" ~ 1L,
          step_variable == "step2" ~ 2L,
          step_variable == "step3" ~ 3L,
          step_variable == "step4" ~ 4L
        ),
        
        step = paste0(
          "Step ",
          step_number
        ),
        
        # A treatment is a separate node at each step.
        # For example, Tarpeyo at Step 3 and Tarpeyo at Step 4
        # are represented as different nodes.
        node_key = paste0(
          step,
          "|||",
          drug_category
        ),
        
        node_label = drug_category
      ) %>%
      arrange(
        sequence_id,
        step_number
      )
  })
  
  
  # ----------------------------------------------------------
  # Update treatment dropdown
  # ----------------------------------------------------------
  
  observe({
    
    treatment_choices <- plot_long() %>%
      distinct(
        drug_category
      ) %>%
      arrange(
        drug_category
      ) %>%
      pull(
        drug_category
      )
    
    current_selection <- isolate(
      input$selected_treatment
    )
    
    if (
      is.null(current_selection) ||
      !current_selection %in% treatment_choices
    ) {
      current_selection <- ""
    }
    
    updateSelectizeInput(
      session = session,
      inputId = "selected_treatment",
      choices = c(
        "No treatment selected" = "",
        setNames(
          treatment_choices,
          treatment_choices
        )
      ),
      selected = current_selection,
      server = TRUE
    )
  })
  
  
  # ----------------------------------------------------------
  # Clear treatment and step selection
  # ----------------------------------------------------------
  
  observeEvent(
    input$clear_selection,
    {
      
      updateSelectizeInput(
        session = session,
        inputId = "selected_treatment",
        selected = ""
      )
      
      updateSelectInput(
        session = session,
        inputId = "selected_step",
        selected = "all"
      )
    }
  )
  
  
  # ----------------------------------------------------------
  # Identify complete pathways matching treatment and step
  # ----------------------------------------------------------
  
  selected_pathway_ids <- reactive({
    
    selected_treatment <- input$selected_treatment
    selected_step <- input$selected_step
    
    if (
      is.null(selected_treatment) ||
      selected_treatment == ""
    ) {
      return(integer(0))
    }
    
    selected_rows <- plot_long() %>%
      filter(
        drug_category == selected_treatment
      )
    
    if (
      !is.null(selected_step) &&
      selected_step != "all"
    ) {
      
      selected_rows <- selected_rows %>%
        filter(
          step == selected_step
        )
    }
    
    selected_rows %>%
      distinct(
        sequence_id
      ) %>%
      pull(
        sequence_id
      )
  })
  
  
  # ----------------------------------------------------------
  # Create treatment nodes
  # ----------------------------------------------------------
  
  sankey_nodes <- reactive({
    
    long_dat <- plot_long()
    
    nodes <- long_dat %>%
      distinct(
        node_key,
        node_label,
        drug_category,
        step,
        step_number
      ) %>%
      arrange(
        step_number,
        node_label
      ) %>%
      mutate(
        node_id = row_number() - 1L,
        
        hover_text = paste0(
          "<b>Treatment:</b> ",
          drug_category,
          "<br>",
          "<b>Position:</b> ",
          step
        )
      )
    
    # Create one consistent color for each treatment category
    treatment_levels <- sort(
      unique(nodes$drug_category)
    )
    
    category_colors <- grDevices::hcl.colors(
      n = max(
        length(treatment_levels),
        1
      ),
      palette = "Set 2"
    )
    
    color_lookup <- setNames(
      category_colors[
        seq_along(treatment_levels)
      ],
      treatment_levels
    )
    
    nodes %>%
      mutate(
        node_color = unname(
          color_lookup[drug_category]
        )
      )
  })
  
  
  # ----------------------------------------------------------
  # Create pathway connectors
  # ----------------------------------------------------------
  
  sankey_links <- reactive({
    
    long_dat <- plot_long()
    nodes <- sankey_nodes()
    selected_ids <- selected_pathway_ids()
    
    selected_treatment <- input$selected_treatment
    
    links <- long_dat %>%
      group_by(
        sequence_id
      ) %>%
      arrange(
        step_number,
        .by_group = TRUE
      ) %>%
      mutate(
        target_node_key = lead(node_key),
        target_treatment = lead(drug_category),
        target_step = lead(step)
      ) %>%
      ungroup() %>%
      filter(
        !is.na(target_node_key)
      ) %>%
      left_join(
        nodes %>%
          select(
            source_node_key = node_key,
            source = node_id
          ),
        by = c(
          "node_key" = "source_node_key"
        )
      ) %>%
      left_join(
        nodes %>%
          select(
            target_node_key = node_key,
            target = node_id
          ),
        by = "target_node_key"
      ) %>%
      mutate(
        pathway_selected =
          sequence_id %in% selected_ids,
        
        hover_text = paste0(
          "<b>Full pathway:</b> ",
          treatment_sequence_4steps,
          "<br>",
          "<b>Patients:</b> ",
          scales::comma(n_patients),
          "<br>",
          "<b>Connection:</b> ",
          drug_category,
          " → ",
          target_treatment,
          "<br>",
          "<b>Position:</b> ",
          step,
          " → ",
          target_step
        )
      )
    
    # No treatment selected: show every pathway clearly
    if (
      is.null(selected_treatment) ||
      selected_treatment == ""
    ) {
      
      links <- links %>%
        mutate(
          link_color = "rgba(80, 100, 140, 0.45)"
        )
      
    } else {
      
      # Selected pathways are dark and prominent.
      # Nonselected pathways remain visible but are dimmed.
      links <- links %>%
        mutate(
          link_color = if_else(
            pathway_selected,
            "rgba(25, 25, 25, 0.85)",
            "rgba(170, 170, 170, 0.12)"
          )
        )
    }
    
    links
  })
  
  
  # ----------------------------------------------------------
  # Create graph subtitle
  # ----------------------------------------------------------
  
  subtitle_text <- reactive({
    
    selected_treatment <- input$selected_treatment
    selected_step <- input$selected_step
    
    group_text <- case_when(
      
      input$tarpeyo_group == "later" ~
        "Tarpeyo initiated after the first treatment step",
      
      input$tarpeyo_group == "start" ~
        "Treatment pathways starting with Tarpeyo",
      
      TRUE ~
        "All Tarpeyo-containing treatment pathways"
    )
    
    highlight_text <- if (
      is.null(selected_treatment) ||
      selected_treatment == ""
    ) {
      
      "Select a treatment category and optional step"
      
    } else if (
      is.null(selected_step) ||
      selected_step == "all"
    ) {
      
      paste0(
        "Highlighted pathways containing ",
        selected_treatment,
        " at any step"
      )
      
    } else {
      
      paste0(
        "Highlighted pathways with ",
        selected_treatment,
        " at ",
        selected_step
      )
    }
    
    paste0(
      group_text,
      " — Top ",
      nrow(plot_data()),
      " pathways",
      "<br>",
      highlight_text,
      "<br>",
      "<sup>Hover over a connector to view the complete pathway.</sup>"
    )
  })
  
  
  # ----------------------------------------------------------
  # Display native Plotly Sankey graph
  # ----------------------------------------------------------
  
  output$sankey_plot <- renderPlotly({
    
    nodes <- sankey_nodes()
    links <- sankey_links()
    
    validate(
      need(
        nrow(nodes) > 0,
        "No treatment pathways match the selected criteria."
      ),
      
      need(
        nrow(links) > 0,
        paste0(
          "The selected data contain no connections between ",
          "treatment steps."
        )
      )
    )
    
    plot_ly(
      type = "sankey",
      orientation = "h",
      
      arrangement = "snap",
      
      valueformat = ",",
      
      node = list(
        pad = input$node_padding,
        thickness = input$node_width,
        line = list(
          color = "rgba(80, 80, 80, 0.60)",
          width = 0.5
        ),
        label = nodes$node_label,
        color = nodes$node_color,
        customdata = nodes$hover_text,
        
        hovertemplate = paste0(
          "%{customdata}",
          "<br>",
          "<b>Total patients through node:</b> ",
          "%{value:,}",
          "<extra></extra>"
        )
      ),
      
      link = list(
        source = links$source,
        target = links$target,
        value = links$n_patients,
        color = links$link_color,
        customdata = links$hover_text,
        
        hovertemplate = paste0(
          "%{customdata}",
          "<extra></extra>"
        )
      )
    ) %>%
      
      layout(
        title = list(
          text = paste0(
            "<b>Tarpeyo Treatment Pathways ",
            "After first IgAN Diagnosis </b>",
            "<br>",
            "<span style='font-size:12px'>",
            subtitle_text(),
            "</span>"
          ),
          x = 0.02,
          xanchor = "left"
        ),
        
        font = list(
          size = 12
        ),
        
        margin = list(
          l = 40,
          r = 40,
          t = 145,
          b = 50
        ),
        
        hoverlabel = list(
          align = "left",
          bgcolor = "white",
          bordercolor = "gray"
        )
      ) %>%
      
      config(
        displaylogo = FALSE,
        responsive = TRUE,
        
        toImageButtonOptions = list(
          format = "png",
          filename = paste0(
            "tarpeyo_treatment_pathways_",
            Sys.Date()
          ),
          scale = 2
        ),
        
        modeBarButtonsToRemove = c(
          "select2d",
          "lasso2d",
          "autoScale2d",
          "toggleSpikelines"
        )
      )
  })
  
  
  # ----------------------------------------------------------
  # Display highlighted-pathway summary
  # ----------------------------------------------------------
  
  output$selected_summary <- renderText({
    
    selected_treatment <- input$selected_treatment
    selected_step <- input$selected_step
    
    if (
      is.null(selected_treatment) ||
      selected_treatment == ""
    ) {
      
      return(
        paste0(
          "No treatment category selected.\n",
          "Select a treatment category and optional step."
        )
      )
    }
    
    selected_ids <- selected_pathway_ids()
    
    selected_data <- plot_data() %>%
      filter(
        sequence_id %in% selected_ids
      ) %>%
      arrange(
        desc(n_patients)
      )
    
    step_text <- if (
      is.null(selected_step) ||
      selected_step == "all"
    ) {
      "Any step"
    } else {
      selected_step
    }
    
    if (nrow(selected_data) == 0) {
      
      return(
        paste0(
          "No pathways contain ",
          selected_treatment,
          " at ",
          step_text,
          "."
        )
      )
    }
    
    pathway_list <- selected_data %>%
      transmute(
        pathway_text = paste0(
          treatment_sequence_4steps,
          " (n=",
          scales::comma(n_patients),
          ")"
        )
      ) %>%
      pull(
        pathway_text
      )
    
    paste0(
      "Selected treatment: ",
      selected_treatment,
      "\n",
      "Selected step: ",
      step_text,
      "\n",
      "Number of highlighted pathways: ",
      nrow(selected_data),
      "\n",
      "Total patients across highlighted pathways: ",
      scales::comma(
        sum(
          selected_data$n_patients,
          na.rm = TRUE
        )
      ),
      "\n\n",
      "Highlighted pathways:\n",
      paste(
        pathway_list,
        collapse = "\n"
      )
    )
  })
}


# ============================================================
# Run application
# ============================================================

# access_gate turns the UI into a function of the request. It reads the
# identity the ALB injected, and serves either the dashboard or a refusal page.
# Refusal happens at page load, so an unauthorised caller never reaches the
# server function or the underlying data. With ACCESS_MODE unset it is a no-op,
# so running this locally is unchanged.
shinyApp(
  ui = access_gate(main_ui),
  server = server
)

#shiny::runApp(
#     appDir = "G:/Shared drives/StratePulse/Veloxis Komodo Data/PROGRAMS/Yi/app.R",
#     host = "0.0.0.0",
#     port = 3838,
#     launch.browser = TRUE
# )
