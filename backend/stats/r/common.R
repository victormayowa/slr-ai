# ---- Shared helpers for OmniReview analyses ----
suppressPackageStartupMessages(library(jsonlite))

spec <- jsonlite::fromJSON("spec.json", simplifyVector = FALSE)
data_rows <- jsonlite::fromJSON("data.json", simplifyDataFrame = TRUE)
if (length(data_rows) == 0) data_rows <- data.frame()
set.seed(if (is.null(spec$seed)) 20260915L else as.integer(spec$seed))
dir.create("plots", showWarnings = FALSE)

results <- list(warnings = list(), notes = list())
RATIO_MEASURES <- c("OR", "RR", "HR", "IRR", "ROM", "PETO_OR")
LEVEL <- if (is.null(spec$level)) 95 else as.numeric(spec$level)

note_warning <- function(message) {
  results$warnings[[length(results$warnings) + 1]] <<- message
}

add_note <- function(message) {
  results$notes[[length(results$notes) + 1]] <<- message
}

spec_value <- function(name, default = NULL) {
  value <- spec[[name]]
  if (is.null(value)) default else value
}

column <- function(frame, name) {
  if (name %in% names(frame)) suppressWarnings(as.numeric(frame[[name]])) else rep(NA_real_, nrow(frame))
}

save_plot <- function(name, draw, width = 8, height = 6) {
  formats <- unlist(spec_value("plot_formats", list("svg", "png", "pdf")))
  for (fmt in formats) {
    path <- file.path("plots", paste0(name, ".", fmt))
    opened <- tryCatch({
      if (fmt == "svg") {
        svglite::svglite(path, width = width, height = height)
      } else if (fmt == "png") {
        grDevices::png(path, width = width, height = height, units = "in", res = 300)
      } else if (fmt == "tiff") {
        grDevices::tiff(path, width = width, height = height, units = "in", res = 600, compression = "lzw")
      } else {
        grDevices::pdf(path, width = width, height = height)
      }
      TRUE
    }, error = function(e) {
      note_warning(paste0("The ", fmt, " device isn't available: ", conditionMessage(e)))
      FALSE
    })
    if (!opened) next
    tryCatch(draw(), error = function(e) note_warning(paste0("Plot ", name, " failed: ", conditionMessage(e))),
             finally = grDevices::dev.off())
  }
}

finish <- function() {
  results$package_versions <<- lapply(
    Filter(function(p) requireNamespace(p, quietly = TRUE), c("metafor", "meta", "netmeta", "mada", "clubSandwich", "lme4", "bayesmeta", "robvis")),
    function(p) as.character(utils::packageVersion(p))
  )
  writeLines(jsonlite::toJSON(results, auto_unbox = TRUE, digits = NA, null = "null", na = "null", pretty = TRUE), "results.json")
  writeLines(utils::capture.output(utils::sessionInfo()), "session_info.txt")
}

run_analysis <- function(analysis) {
  outcome <- tryCatch(
    withCallingHandlers(analysis(), warning = function(w) {
      note_warning(conditionMessage(w))
      invokeRestart("muffleWarning")
    }),
    error = function(e) {
      writeLines(conditionMessage(e), "error.txt")
      quit(save = "no", status = 1)
    }
  )
  finish()
  invisible(outcome)
}

z_crit <- function(level = LEVEL) stats::qnorm(1 - (1 - level / 100) / 2)

# Effect sizes for each study from arm-level data (binary or continuous), 2x2 counts, or estimates with CIs or SEs.
compute_effects <- function(d, measure = spec$measure, data_type = spec$data_type) {
  add <- as.numeric(spec_value("zero_correction", 0.5))
  to <- spec_value("zero_correction_to", "only0")
  if (data_type == "binary") {
    es <- metafor::escalc(measure = measure, ai = column(d, "ai"), n1i = column(d, "n1i"), ci = column(d, "ci"),
                          n2i = column(d, "n2i"), data = d, add = add, to = to, slab = d$study)
  } else if (data_type == "continuous") {
    es <- metafor::escalc(measure = measure, m1i = column(d, "m1i"), sd1i = column(d, "sd1i"), n1i = column(d, "n1i"),
                          m2i = column(d, "m2i"), sd2i = column(d, "sd2i"), n2i = column(d, "n2i"), data = d, slab = d$study)
  } else {
    ratio <- measure %in% RATIO_MEASURES
    estimate <- column(d, "estimate")
    lower <- column(d, "ci_lower")
    upper <- column(d, "ci_upper")
    yi <- column(d, "yi")
    sei <- column(d, "sei")
    yi <- ifelse(is.na(yi), if (ratio) log(estimate) else estimate, yi)
    width <- if (ratio) log(upper) - log(lower) else upper - lower
    sei <- ifelse(is.na(sei), width / (2 * stats::qnorm(0.975)), sei)
    es <- metafor::escalc(measure = "GEN", yi = yi, sei = sei, data = d, slab = d$study)
  }
  es
}

summarise_fit <- function(fit, transform = FALSE, prediction = TRUE) {
  out <- list(
    k = fit$k,
    estimate = as.numeric(fit$beta[1]),
    se = as.numeric(fit$se[1]),
    ci_lower = as.numeric(fit$ci.lb[1]),
    ci_upper = as.numeric(fit$ci.ub[1]),
    statistic = as.numeric(fit$zval[1]),
    p_value = as.numeric(fit$pval[1]),
    test = if (is.null(fit$test)) "z" else fit$test,
    method = if (inherits(fit, "rma.mh")) "MH" else if (inherits(fit, "rma.peto")) "Peto" else if (inherits(fit, "rma.glmm")) "GLMM" else if (is.null(fit$method)) class(fit)[1] else fit$method
  )
  if (!is.null(fit$tau2)) out$tau2 <- as.numeric(fit$tau2)
  if (!is.null(fit$tau2) && !is.null(fit$tau2)) out$tau <- sqrt(max(0, as.numeric(fit$tau2)))
  if (!is.null(fit$I2)) out$I2 <- as.numeric(fit$I2)
  if (!is.null(fit$H2)) out$H2 <- as.numeric(fit$H2)
  if (!is.null(fit$QE)) {
    out$Q <- as.numeric(fit$QE)
    out$Q_p_value <- as.numeric(fit$QEp)
    out$Q_df <- fit$k - length(fit$beta)
  }
  if (prediction && inherits(fit, "rma.uni") && !identical(fit$method, "FE")) {
    pred <- stats::predict(fit, level = LEVEL)
    if (!is.null(pred$pi.lb)) {
      out$pi_lower <- as.numeric(pred$pi.lb[1])
      out$pi_upper <- as.numeric(pred$pi.ub[1])
    }
    ci_tau <- tryCatch(stats::confint(fit, level = LEVEL)$random, error = function(e) NULL)
    if (!is.null(ci_tau)) {
      out$tau2_ci <- as.numeric(ci_tau["tau^2", c("ci.lb", "ci.ub")])
      out$I2_ci <- as.numeric(ci_tau["I^2(%)", c("ci.lb", "ci.ub")])
    }
  }
  if (transform) {
    out$exp_estimate <- exp(out$estimate)
    out$exp_ci_lower <- exp(out$ci_lower)
    out$exp_ci_upper <- exp(out$ci_upper)
    if (!is.null(out$pi_lower)) {
      out$exp_pi_lower <- exp(out$pi_lower)
      out$exp_pi_upper <- exp(out$pi_upper)
    }
  }
  out
}

study_table <- function(es, weights = NULL, transform = FALSE) {
  z <- z_crit()
  rows <- lapply(seq_len(nrow(es)), function(i) {
    yi <- as.numeric(es$yi[i])
    se <- sqrt(as.numeric(es$vi[i]))
    row <- list(study = as.character(es$study[i]), study_id = if ("study_id" %in% names(es)) es$study_id[i] else NA,
                yi = yi, se = se, ci_lower = yi - z * se, ci_upper = yi + z * se,
                weight = if (is.null(weights)) NA else as.numeric(weights[i]))
    if (transform) {
      row$exp_yi <- exp(yi)
      row$exp_ci_lower <- exp(row$ci_lower)
      row$exp_ci_upper <- exp(row$ci_upper)
    }
    row
  })
  rows
}
