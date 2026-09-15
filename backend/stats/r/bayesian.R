# ---- Bayesian random-effects meta-analysis with bayesmeta: posterior for the overall effect and heterogeneity,
# with a half-normal prior for tau and a vague or normal prior for the effect. ----
suppressPackageStartupMessages({
  library(metafor)
  library(bayesmeta)
})

run_analysis(function() {
  es <- compute_effects(data_rows)
  es <- es[!is.na(es$yi) & !is.na(es$vi), ]
  if (nrow(es) < 2) stop("At least two studies with usable data are needed")
  scale <- as.numeric(spec_value("tau_prior_scale", 0.5))
  mu_sd <- spec$mu_prior_sd
  fit <- bayesmeta(
    y = as.numeric(es$yi), sigma = sqrt(as.numeric(es$vi)), labels = as.character(es$study),
    tau.prior = function(t) dhalfnormal(t, scale = scale),
    mu.prior.mean = if (is.null(mu_sd)) NA else 0, mu.prior.sd = if (is.null(mu_sd)) NA else as.numeric(mu_sd)
  )
  sm <- fit$summary
  row_of <- function(pattern) grep(pattern, rownames(sm), fixed = TRUE)[1]
  transform <- spec$measure %in% RATIO_MEASURES
  out <- list(
    k = nrow(es),
    mu_median = sm[row_of("median"), "mu"],
    mu_mean = sm[row_of("mean"), "mu"],
    mu_lower = sm[row_of("lower"), "mu"],
    mu_upper = sm[row_of("upper"), "mu"],
    tau_median = sm[row_of("median"), "tau"],
    tau_lower = sm[row_of("lower"), "tau"],
    tau_upper = sm[row_of("upper"), "tau"],
    prediction_lower = sm[row_of("lower"), "theta"],
    prediction_upper = sm[row_of("upper"), "theta"],
    probability_effect_below_zero = fit$pposterior(mu = 0)
  )
  if (transform) {
    out$exp_mu_median <- exp(out$mu_median)
    out$exp_mu_lower <- exp(out$mu_lower)
    out$exp_mu_upper <- exp(out$mu_upper)
    out$exp_prediction_lower <- exp(out$prediction_lower)
    out$exp_prediction_upper <- exp(out$prediction_upper)
  }
  results$summary <<- out
  results$priors <<- list(
    tau = paste0("half-normal(scale = ", scale, ")"),
    mu = if (is.null(mu_sd)) "uniform (improper)" else paste0("normal(0, ", mu_sd, ")")
  )
  results$studies <<- study_table(es, NULL, transform)
  save_plot("forest", function() forestplot(fit), width = 10, height = max(4, 0.35 * nrow(es) + 3))
  save_plot("posterior_effect", function() plot(fit, which = 3, prior = TRUE))
  save_plot("posterior_heterogeneity", function() plot(fit, which = 4, prior = TRUE))
})
