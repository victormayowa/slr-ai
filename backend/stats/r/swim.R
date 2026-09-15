# ---- Synthesis without meta-analysis (SWiM): vote counting based on the direction of effect with a sign test, and an
# effect direction plot (Cochrane Handbook section 12.2.2; Campbell et al. BMJ 2020;368:l6890). ----
suppressPackageStartupMessages(library(ggplot2))

run_analysis(function() {
  d <- data_rows
  if (nrow(d) == 0) stop("Record the direction of effect for at least one study")
  benefit <- sum(d$direction == "benefit")
  harm <- sum(d$direction == "harm")
  votes <- list(
    studies = nrow(d), benefit = benefit, harm = harm,
    no_clear_difference = sum(d$direction == "no_clear_difference"), conflicting = sum(d$direction == "conflicting"),
    method = "Sign test of studies showing benefit against those showing harm"
  )
  if (benefit + harm > 0) {
    test <- stats::binom.test(benefit, benefit + harm, p = 0.5, conf.level = LEVEL / 100)
    votes$proportion_benefit <- as.numeric(test$estimate)
    votes$proportion_ci <- as.numeric(test$conf.int)
    votes$p_value <- test$p.value
  }
  results$vote_counting <<- votes
  domains <- unique(d$outcome_domain)
  results$by_outcome_domain <<- lapply(domains, function(domain) {
    part <- d[d$outcome_domain == domain, ]
    list(outcome_domain = domain, studies = nrow(part), benefit = sum(part$direction == "benefit"),
         harm = sum(part$direction == "harm"), no_clear_difference = sum(part$direction == "no_clear_difference"),
         conflicting = sum(part$direction == "conflicting"))
  })
  shapes <- c(benefit = 24, harm = 25, no_clear_difference = 21, conflicting = 23)
  fills <- c(benefit = "#02C100", harm = "#BF0000", no_clear_difference = "#9E9E9E", conflicting = "#E2DF07")
  plot <- ggplot(d, aes(x = outcome_domain, y = factor(study, levels = rev(unique(study))))) +
    geom_point(aes(shape = direction, fill = direction), size = 5) +
    scale_shape_manual(values = shapes, drop = FALSE) +
    scale_fill_manual(values = fills, drop = FALSE) +
    labs(x = NULL, y = NULL, shape = "Direction of effect", fill = "Direction of effect") +
    theme_minimal()
  save_plot("effect_direction", function() print(plot), width = 4 + 1.5 * length(domains), height = max(3, 0.35 * nrow(d) + 2))
})
