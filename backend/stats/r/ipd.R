# ---- Individual participant data meta-analysis: two-stage (a model per study, then random-effects pooling) and
# one-stage (a mixed model with study-stratified intercepts and a random treatment effect, lme4). ----
suppressPackageStartupMessages({
  library(metafor)
  library(lme4)
})

run_analysis(function() {
  ipd <- utils::read.csv("ipd.csv", stringsAsFactors = FALSE)
  if (nrow(ipd) == 0) stop("No participant data were provided")
  binary <- identical(spec$outcome_type, "binary")
  adjust <- unlist(spec$adjust_for)
  covariates <- if (length(adjust)) paste(" +", paste(adjust, collapse = " + ")) else ""
  family <- if (binary) stats::binomial() else stats::gaussian()

  per_study <- lapply(split(ipd, ipd$study), function(s) {
    model <- tryCatch(stats::glm(stats::as.formula(paste0("outcome ~ treatment", covariates)), family = family, data = s),
                      error = function(e) NULL)
    if (is.null(model)) return(NULL)
    co <- summary(model)$coefficients
    if (!"treatment" %in% rownames(co)) return(NULL)
    list(study = as.character(s$study[1]), n = nrow(s), yi = co["treatment", 1], sei = co["treatment", 2])
  })
  per_study <- Filter(Negate(is.null), per_study)
  if (length(per_study) < 2) stop("At least two studies need estimable treatment effects")
  two <- data.frame(study = vapply(per_study, function(x) x$study, ""), yi = vapply(per_study, function(x) x$yi, 0),
                    sei = vapply(per_study, function(x) x$sei, 0))
  fit <- rma(yi = two$yi, sei = two$sei, method = "REML", test = "knha", slab = two$study)
  results$two_stage <<- c(list(studies = per_study), summarise_fit(fit, transform = binary))
  save_plot("forest_two_stage", function() {
    args <- list(fit, header = c("Study", if (binary) "Odds ratio [95% CI]" else "Mean difference [95% CI]"))
    if (binary) args$atransf <- exp
    do.call(metafor::forest, args)
  }, width = 9, height = max(4, 0.35 * nrow(two) + 3))

  formula <- stats::as.formula(paste0("outcome ~ factor(study) + treatment", covariates, " + (0 + treatment | study)"))
  one <- if (binary) lme4::glmer(formula, data = ipd, family = stats::binomial()) else lme4::lmer(formula, data = ipd, REML = TRUE)
  co <- summary(one)$coefficients["treatment", ]
  z <- z_crit()
  out <- list(model = paste(deparse(formula), collapse = " "), participants = nrow(ipd), k = length(unique(ipd$study)),
              estimate = as.numeric(co[1]), se = as.numeric(co[2]),
              ci_lower = as.numeric(co[1] - z * co[2]), ci_upper = as.numeric(co[1] + z * co[2]),
              tau = as.numeric(attr(lme4::VarCorr(one)$study, "stddev")[1]))
  if (binary) {
    out$exp_estimate <- exp(out$estimate)
    out$exp_ci_lower <- exp(out$ci_lower)
    out$exp_ci_upper <- exp(out$ci_upper)
  }
  results$one_stage <<- out
})
