# ---- Dependent effect sizes: a multilevel model (effects within studies) with robust variance estimation and
# small-sample corrections (CR2 and Satterthwaite degrees of freedom, clubSandwich). ----
suppressPackageStartupMessages({
  library(metafor)
  library(clubSandwich)
})

run_analysis(function() {
  es <- compute_effects(data_rows)
  es <- es[!is.na(es$yi) & !is.na(es$vi), ]
  studies <- length(unique(es$study))
  if (studies < 2) stop("At least two studies with usable effects are needed")
  es$es_id <- seq_len(nrow(es))
  fit <- rma.mv(yi, vi, random = ~ 1 | study / es_id, data = es, method = "REML")
  robust <- coef_test(fit, vcov = "CR2", cluster = es$study)
  interval <- conf_int(fit, vcov = "CR2", cluster = es$study, level = LEVEL / 100)
  transform <- spec$measure %in% RATIO_MEASURES
  df <- as.numeric(robust$df_Satt[1])
  if (!is.na(df) && df < 4) note_warning("Satterthwaite degrees of freedom are below 4, so robust p-values and intervals are unreliable.")
  out <- list(
    k_effects = nrow(es), k_studies = studies,
    estimate = as.numeric(robust$beta[1]), se = as.numeric(robust$SE[1]), statistic = as.numeric(robust$tstat[1]),
    df = df, p_value = as.numeric(robust$p_Satt[1]),
    ci_lower = as.numeric(interval$CI_L[1]), ci_upper = as.numeric(interval$CI_U[1]),
    sigma2_between_studies = as.numeric(fit$sigma2[1]), sigma2_within_studies = as.numeric(fit$sigma2[2]),
    model_based = list(estimate = as.numeric(fit$beta[1]), se = as.numeric(fit$se[1]),
                       ci_lower = as.numeric(fit$ci.lb[1]), ci_upper = as.numeric(fit$ci.ub[1]))
  )
  if (transform) {
    out$exp_estimate <- exp(out$estimate)
    out$exp_ci_lower <- exp(out$ci_lower)
    out$exp_ci_upper <- exp(out$ci_upper)
  }
  results$summary <<- out
  results$effects <<- study_table(es, NULL, transform)
  save_plot("forest", function() {
    args <- list(fit, header = c("Study (effect)", "Estimate [95% CI]"), slab = paste0(es$study, " (", es$effect, ")"))
    if (transform) args$atransf <- exp
    do.call(metafor::forest, args)
  }, width = 10, height = max(4, 0.3 * nrow(es) + 3))
})
