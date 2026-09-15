# ---- Risk of bias plots: robvis where it supports the tool, otherwise the same layout drawn with ggplot2 ----
suppressPackageStartupMessages(library(ggplot2))

COLOURS <- c("Low" = "#02C100", "Some concerns" = "#E2DF07", "Moderate" = "#E2DF07", "Unclear" = "#E2DF07",
             "High" = "#BF0000", "Serious" = "#BF0000", "Critical" = "#820000", "Very high" = "#820000",
             "No information" = "#4EA1F7")

manual_traffic_light <- function(d, labels) {
  domains <- c(paste0("D", seq_along(labels)), "Overall")
  long <- do.call(rbind, lapply(domains, function(col) data.frame(Study = d$Study, Domain = col, Judgement = d[[col]])))
  long$Domain <- factor(long$Domain, levels = domains)
  long$Study <- factor(long$Study, levels = rev(unique(d$Study)))
  ggplot(long, aes(x = Domain, y = Study)) +
    geom_point(aes(colour = Judgement), size = 7) +
    scale_colour_manual(values = COLOURS, drop = TRUE) +
    labs(x = NULL, y = NULL, colour = "Judgement",
         caption = paste(paste0("D", seq_along(labels), ": ", labels), collapse = "\n")) +
    theme_minimal() +
    theme(panel.grid = element_blank(), plot.caption = element_text(hjust = 0))
}

manual_summary <- function(d, labels) {
  domains <- c(paste0("D", seq_along(labels)), "Overall")
  names_for <- c(labels, "Overall risk of bias")
  long <- do.call(rbind, lapply(seq_along(domains), function(i) data.frame(Domain = names_for[i], Judgement = d[[domains[i]]])))
  long$Domain <- factor(long$Domain, levels = rev(names_for))
  ggplot(long, aes(y = Domain, fill = Judgement)) +
    geom_bar(position = "fill", width = 0.7) +
    scale_fill_manual(values = COLOURS) +
    scale_x_continuous(labels = function(x) paste0(round(x * 100), "%")) +
    labs(x = NULL, y = NULL, fill = "Judgement") +
    theme_minimal()
}

run_analysis(function() {
  d <- data_rows
  labels <- unlist(spec$domain_labels)
  kind <- spec$kind
  tool <- spec$robvis_tool
  robvis_ok <- requireNamespace("robvis", quietly = TRUE) && tool %in% c("ROB2", "ROBINS-I", "QUADAS-2", "QUIPS")
  build <- function() {
    if (robvis_ok) {
      plot <- tryCatch(
        if (kind == "summary") robvis::rob_summary(d, tool = tool, weighted = FALSE) else robvis::rob_traffic_light(d, tool = tool),
        error = function(e) {
          note_warning(paste("robvis couldn't draw this plot, so the standard layout was drawn instead:", conditionMessage(e)))
          NULL
        }
      )
      if (!is.null(plot)) return(plot)
    }
    if (kind == "summary") manual_summary(d, labels) else manual_traffic_light(d, labels)
  }
  plot <- build()
  height <- if (kind == "summary") 3 + 0.3 * length(labels) else max(3, 0.4 * nrow(d) + 2.5)
  save_plot(kind, function() print(plot), width = if (kind == "summary") 9 else 7 + 0.4 * length(labels), height = height)
  results$studies <<- nrow(d)
  results$used_robvis <<- robvis_ok
})
