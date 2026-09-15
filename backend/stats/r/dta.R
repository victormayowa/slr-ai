# ---- Diagnostic test accuracy meta-analysis with mada: the bivariate random-effects model (Reitsma et al. 2005),
# summary sensitivity and specificity, HSROC parameters, AUC, SROC curve, and paired forest plots. ----
suppressPackageStartupMessages(library(mada))

run_analysis(function() {
  d <- data.frame(study = as.character(data_rows$study), TP = column(data_rows, "TP"), FN = column(data_rows, "FN"),
                  FP = column(data_rows, "FP"), TN = column(data_rows, "TN"))
  complete <- stats::complete.cases(d[, c("TP", "FN", "FP", "TN")])
  if (any(!complete)) add_note(paste("Studies without a complete 2x2 table were left out:", paste(d$study[!complete], collapse = ", ")))
  d <- d[complete, ]
  if (nrow(d) < 3) stop("At least three studies with complete 2x2 tables are needed for the bivariate model")
  fit <- reitsma(d)
  s <- summary(fit)
  coefs <- s$coefficients
  pick <- function(row, col) as.numeric(coefs[grep(row, rownames(coefs), fixed = TRUE)[1], col])
  ci_cols <- grep("ci", colnames(coefs))
  fpr_ci <- c(pick("false pos. rate", ci_cols[1]), pick("false pos. rate", ci_cols[2]))
  results$summary <<- list(
    k = nrow(d),
    sensitivity = pick("sensitivity", 1),
    sensitivity_ci = c(pick("sensitivity", ci_cols[1]), pick("sensitivity", ci_cols[2])),
    false_positive_rate = pick("false pos. rate", 1),
    false_positive_rate_ci = fpr_ci,
    specificity = 1 - pick("false pos. rate", 1),
    specificity_ci = rev(1 - fpr_ci),
    auc = tryCatch(as.numeric(AUC(fit)$AUC), error = function(e) NULL),
    hsroc = tryCatch(as.list(s$HSROC), error = function(e) NULL)
  )
  uni <- madad(d)
  results$studies <<- lapply(seq_len(nrow(d)), function(i) list(
    study = d$study[i], TP = d$TP[i], FN = d$FN[i], FP = d$FP[i], TN = d$TN[i],
    sensitivity = uni$sens$sens[i], sensitivity_ci = as.numeric(uni$sens$sens.ci[i, ]),
    specificity = uni$spec$spec[i], specificity_ci = as.numeric(uni$spec$spec.ci[i, ])
  ))
  save_plot("sroc", function() {
    plot(fit, sroclwd = 2, main = "Summary ROC curve")
    graphics::points(fpr(d), sens(d), pch = 1)
    graphics::legend("bottomright", c("Studies", "Summary estimate and 95% confidence region"), pch = c(1, 1), bty = "n")
  }, width = 7, height = 7)
  save_plot("forest_sensitivity", function() forest(uni, type = "sens", snames = d$study), height = max(4, 0.3 * nrow(d) + 2))
  save_plot("forest_specificity", function() forest(uni, type = "spec", snames = d$study), height = max(4, 0.3 * nrow(d) + 2))
})
