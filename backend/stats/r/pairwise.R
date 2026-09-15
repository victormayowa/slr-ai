# ---- Pairwise meta-analysis with metafor: effect sizes, fixed or random effects (DL, REML, PM, ...; Hartung-Knapp),
# Mantel-Haenszel, Peto, or GLMM; prediction intervals; heterogeneity; subgroups; meta-regression; sensitivity
# analyses; and small-study effects and publication bias. ----
suppressPackageStartupMessages(library(metafor))

run_analysis(function() {
  d <- data_rows
  if (nrow(d) == 0) stop("No studies have the data this analysis needs")
  measure <- spec$measure
  model <- spec_value("model", "random")
  tau_method <- spec_value("tau_method", "REML")
  hksj <- isTRUE(spec_value("hksj", TRUE)) && model == "random"
  es <- compute_effects(d)
  usable <- !is.na(es$yi) & !is.na(es$vi)
  if (any(!usable)) add_note(paste("Studies without a computable effect were left out:", paste(es$study[!usable], collapse = ", ")))
  es <- es[usable, ]
  if (nrow(es) < 2) stop("At least two studies with usable data are needed to pool results")
  transform <- measure %in% RATIO_MEASURES || (identical(spec$data_type, "generic") && isTRUE(spec$ratio))
  binary <- identical(spec$data_type, "binary")

  fit_model <- function(dat, method = tau_method, fit_type = model) {
    if (fit_type == "fixed") {
      return(rma(yi, vi, data = dat, method = "FE", slab = dat$study))
    }
    if (fit_type == "mantel_haenszel") {
      if (!binary) stop("Mantel-Haenszel needs binary arm-level data")
      return(rma.mh(ai = ai, n1i = n1i, ci = ci, n2i = n2i, data = dat, measure = measure, slab = dat$study))
    }
    if (fit_type == "peto") {
      if (!binary) stop("The Peto method needs binary arm-level data")
      return(rma.peto(ai = ai, n1i = n1i, ci = ci, n2i = n2i, data = dat, slab = dat$study))
    }
    if (fit_type == "glmm") {
      if (!binary || measure != "OR") stop("The GLMM option fits odds ratios from binary arm-level data")
      return(rma.glmm(measure = "OR", ai = ai, n1i = n1i, ci = ci, n2i = n2i, data = dat, model = "UM.FS", method = "ML"))
    }
    rma(yi, vi, data = dat, method = method, test = if (hksj) "knha" else "z", slab = dat$study)
  }

  fit <- fit_model(es)
  weights <- tryCatch(as.numeric(metafor::weights.rma.uni(fit)), error = function(e) tryCatch(as.numeric(weights(fit)), error = function(e2) NULL))
  results$model <<- list(model = model, tau_method = if (model == "random") tau_method else NULL, hksj = hksj,
                        measure = measure, level = LEVEL, back_transformed = transform)
  results$summary <<- summarise_fit(fit, transform = transform, prediction = isTRUE(spec_value("prediction_interval", TRUE)))
  results$studies <<- study_table(es, weights, transform)

  forest_height <- max(4, 0.32 * nrow(es) + 3)
  save_plot("forest", function() {
    args <- list(fit, header = c("Study", "Estimate [95% CI]"), showweights = !is.null(weights))
    if (transform) args$atransf <- exp
    if (inherits(fit, "rma.uni") && model == "random" && isTRUE(spec_value("prediction_interval", TRUE))) args$addpred <- TRUE
    do.call(metafor::forest, args)
  }, width = 10, height = forest_height)

  # Subgroups: a model per subgroup and a test for subgroup differences.
  subgroup <- spec$subgroup
  if (!is.null(subgroup) && "subgroup" %in% names(es)) {
    groups <- sort(unique(stats::na.omit(as.character(es$subgroup))))
    per_group <- lapply(groups, function(g) {
      part <- es[!is.na(es$subgroup) & es$subgroup == g, ]
      if (nrow(part) < 2) return(list(group = g, k = nrow(part), note = "Fewer than two studies"))
      c(list(group = g), summarise_fit(fit_model(part, fit_type = if (model %in% c("fixed", "random")) model else "random"), transform))
    })
    difference <- tryCatch({
      test <- rma(yi, vi, mods = ~ factor(subgroup), data = es[!is.na(es$subgroup), ],
                  method = if (model == "fixed") "FE" else tau_method, test = if (hksj) "knha" else "z")
      list(statistic = as.numeric(test$QM), df = as.numeric(test$QMdf[1]), p_value = as.numeric(test$QMp))
    }, error = function(e) list(error = conditionMessage(e)))
    results$subgroups <<- list(label = subgroup$label, prespecified = isTRUE(subgroup$prespecified), groups = per_group,
                              test_for_differences = difference)
  }

  # Meta-regression.
  moderators <- spec$moderators
  if (length(moderators) > 0) {
    terms <- vapply(moderators, function(m) if (identical(m$type, "categorical")) paste0("factor(", m$column, ")") else m$column, "")
    formula <- stats::as.formula(paste("~", paste(terms, collapse = " + ")))
    regression <- tryCatch({
      reg <- rma(yi, vi, mods = formula, data = es, method = if (model == "fixed") "FE" else tau_method, test = if (hksj) "knha" else "z")
      coefficients <- lapply(seq_along(reg$beta), function(i) list(term = rownames(reg$beta)[i], estimate = reg$beta[i, 1], se = reg$se[i],
                                                                     ci_lower = reg$ci.lb[i], ci_upper = reg$ci.ub[i], p_value = reg$pval[i]))
      if (length(moderators) == 1 && !identical(moderators[[1]]$type, "categorical")) {
        save_plot("meta_regression", function() metafor::regplot(reg, mod = 2, xlab = moderators[[1]]$label))
      }
      list(k = reg$k, coefficients = coefficients, QM = reg$QM, QM_p_value = reg$QMp,
           tau2 = if (is.null(reg$tau2)) NULL else reg$tau2, R2 = if (is.null(reg$R2)) NULL else reg$R2,
           residual_I2 = if (is.null(reg$I2)) NULL else reg$I2)
    }, error = function(e) list(error = conditionMessage(e)))
    results$meta_regression <<- regression
  }

  # Sensitivity analyses.
  sensitivity <- spec_value("sensitivity", list())
  uni <- inherits(fit, "rma.uni") && !is.null(fit$tau2) || identical(model, "fixed")
  sens <- list()
  if (isTRUE(sensitivity$leave_one_out) && inherits(fit, "rma.uni")) {
    l1o <- leave1out(fit)
    sens$leave_one_out <- lapply(seq_along(l1o$estimate), function(i) {
      row <- list(omitted = as.character(l1o$slab[i]), estimate = l1o$estimate[i], ci_lower = l1o$ci.lb[i], ci_upper = l1o$ci.ub[i],
                  p_value = l1o$pval[i], I2 = if (is.null(l1o$I2)) NA else l1o$I2[i], tau2 = if (is.null(l1o$tau2)) NA else l1o$tau2[i])
      if (transform) row[c("exp_estimate", "exp_ci_lower", "exp_ci_upper")] <- list(exp(row$estimate), exp(row$ci_lower), exp(row$ci_upper))
      row
    })
  }
  if (isTRUE(sensitivity$influence) && inherits(fit, "rma.uni")) {
    inf <- influence(fit)
    table <- as.data.frame(inf$inf)
    residuals <- rstudent(fit)
    sens$influence <- lapply(seq_len(nrow(table)), function(i) list(
      study = rownames(table)[i], rstudent = residuals$z[i], cooks_distance = table$cook.d[i], dffits = table$dffits[i],
      hat = table$hat[i], weight = table$weight[i], tau2_deleted = table$tau2.del[i], influential = isTRUE(inf$is.infl[i]),
      outlier = abs(residuals$z[i]) > 1.96
    ))
    save_plot("influence", function() plot(inf), width = 9, height = 9)
  }
  if (isTRUE(sensitivity$exclude_high_risk_of_bias) && "rob" %in% names(es)) {
    kept <- es[!(es$rob %in% c("high", "serious", "critical", "very_high")), ]
    excluded <- setdiff(as.character(es$study), as.character(kept$study))
    sens$excluding_high_risk_of_bias <- if (nrow(kept) >= 2) {
      c(list(excluded = excluded), summarise_fit(fit_model(kept), transform))
    } else {
      list(excluded = excluded, note = "Fewer than two studies remain")
    }
  }
  alternatives <- unlist(sensitivity$alternative_estimators)
  if (length(alternatives) && model == "random") {
    sens$alternative_estimators <- lapply(alternatives, function(method) {
      tryCatch(c(list(tau_method = method), summarise_fit(fit_model(es, method = method), transform)),
               error = function(e) list(tau_method = method, error = conditionMessage(e)))
    })
  }
  if (length(sensitivity$exclusions)) {
    sens$exclusions <- lapply(sensitivity$exclusions, function(item) {
      kept <- es[!(es$study_id %in% unlist(item$study_ids)), ]
      if (nrow(kept) < 2) return(list(label = item$label, note = "Fewer than two studies remain"))
      c(list(label = item$label), summarise_fit(fit_model(kept), transform))
    })
  }
  if (length(sens)) results$sensitivity <<- sens

  # Small-study effects and publication bias.
  bias <- spec_value("publication_bias", list())
  if (length(bias) && inherits(fit, "rma.uni")) {
    pb <- list(k = fit$k)
    if (fit$k < 10) add_note("Fewer than 10 studies: tests for funnel plot asymmetry have too little power to rule out small-study effects.")
    if (isTRUE(bias$funnel)) {
      save_plot("funnel", function() {
        if (isTRUE(bias$contour)) {
          metafor::funnel(fit, level = c(90, 95, 99), shade = c("white", "gray55", "gray75"), refline = 0, legend = TRUE,
                          atransf = if (transform) exp else NULL)
        } else {
          metafor::funnel(fit, atransf = if (transform) exp else NULL)
        }
      })
    }
    if (isTRUE(bias$egger)) {
      pb$egger <- tryCatch({
        test <- regtest(fit, model = "rma", predictor = "sei")
        list(statistic = test$zval, p_value = test$pval, limit_estimate = if (is.null(test$est)) NULL else test$est)
      }, error = function(e) list(error = conditionMessage(e)))
    }
    if (isTRUE(bias$begg)) {
      pb$begg <- tryCatch({
        test <- ranktest(fit)
        list(kendall_tau = test$tau, p_value = test$pval)
      }, error = function(e) list(error = conditionMessage(e)))
    }
    if (isTRUE(bias$trim_fill)) {
      pb$trim_and_fill <- tryCatch({
        filled <- trimfill(fit)
        save_plot("trim_and_fill", function() metafor::funnel(filled, legend = TRUE, atransf = if (transform) exp else NULL))
        c(list(imputed_studies = filled$k0, side = filled$side), summarise_fit(filled, transform, prediction = FALSE))
      }, error = function(e) list(error = conditionMessage(e)))
    }
    if (isTRUE(bias$pet_peese)) {
      pb$pet_peese <- tryCatch({
        pet <- rma(yi, vi, mods = ~ sqrt(vi), data = es, method = "FE")
        peese <- rma(yi, vi, mods = ~ vi, data = es, method = "FE")
        use_peese <- pet$pval[1] < 0.05
        chosen <- if (use_peese) peese else pet
        out <- list(pet_intercept = pet$beta[1], pet_ci = c(pet$ci.lb[1], pet$ci.ub[1]), pet_p_value = pet$pval[1],
                    peese_intercept = peese$beta[1], peese_ci = c(peese$ci.lb[1], peese$ci.ub[1]), peese_p_value = peese$pval[1],
                    selected = if (use_peese) "PEESE" else "PET", adjusted_estimate = chosen$beta[1],
                    adjusted_ci = c(chosen$ci.lb[1], chosen$ci.ub[1]))
        if (transform) {
          out$exp_adjusted_estimate <- exp(out$adjusted_estimate)
          out$exp_adjusted_ci <- exp(out$adjusted_ci)
        }
        out
      }, error = function(e) list(error = conditionMessage(e)))
    }
    if (isTRUE(bias$selection_model)) {
      pb$selection_model <- tryCatch({
        ml <- rma(yi, vi, data = es, method = "ML")
        selection <- selmodel(ml, type = "stepfun", steps = c(0.025))
        out <- list(type = "step function (one-sided p = 0.025)", estimate = selection$beta[1], ci_lower = selection$ci.lb[1],
                    ci_upper = selection$ci.ub[1], likelihood_ratio_test = selection$LRT, lrt_p_value = selection$LRTp,
                    delta = as.numeric(selection$delta))
        if (transform) out[c("exp_estimate", "exp_ci_lower", "exp_ci_upper")] <- list(exp(out$estimate), exp(out$ci_lower), exp(out$ci_upper))
        out
      }, error = function(e) list(error = conditionMessage(e)))
    }
    results$publication_bias <<- pb
  } else if (length(bias)) {
    add_note("Publication bias analyses use inverse-variance models; they were skipped for this model type.")
  }
})
