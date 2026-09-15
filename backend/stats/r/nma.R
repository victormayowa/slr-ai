# ---- Network meta-analysis with netmeta (frequentist, graph-theoretical): league table, P-scores, node-splitting,
# design-by-treatment inconsistency, network graph, and forest plot against the reference treatment. ----
suppressPackageStartupMessages({
  library(meta)
  library(netmeta)
})

run_analysis(function() {
  d <- data_rows
  if (nrow(d) == 0) stop("No arms have the data this analysis needs")
  measure <- spec$measure
  random <- spec_value("model", "random") != "fixed"
  pairwise_fn <- if (exists("pairwise", envir = asNamespace("meta"), inherits = FALSE)) meta::pairwise else netmeta::pairwise
  p <- if (identical(spec$data_type, "binary")) {
    pairwise_fn(treat = d$treatment, event = column(d, "events"), n = column(d, "n"), studlab = d$study, sm = measure)
  } else {
    pairwise_fn(treat = d$treatment, mean = column(d, "mean"), sd = column(d, "sd"), n = column(d, "n"), studlab = d$study, sm = measure)
  }
  reference <- spec_value("reference_treatment", "")
  fit_net <- function(method) {
    netmeta::netmeta(p, sm = measure, common = !random, random = random,
                     reference.group = if (nzchar(reference)) reference else "", method.tau = method)
  }
  net <- tryCatch(fit_net("REML"), error = function(e) {
    note_warning(paste("REML didn't converge for the network; DerSimonian-Laird was used:", conditionMessage(e)))
    fit_net("DL")
  })
  transform <- measure %in% RATIO_MEASURES
  te <- if (random) net$TE.random else net$TE.common
  lower <- if (random) net$lower.random else net$lower.common
  upper <- if (random) net$upper.random else net$upper.common
  pvalues <- if (random) net$pval.random else net$pval.common
  treatments <- rownames(te)
  league <- list()
  for (a in treatments) for (b in treatments) if (a != b) {
    row <- list(treatment = a, comparator = b, estimate = te[a, b], ci_lower = lower[a, b], ci_upper = upper[a, b], p_value = pvalues[a, b])
    if (transform) row[c("exp_estimate", "exp_ci_lower", "exp_ci_upper")] <- list(exp(te[a, b]), exp(lower[a, b]), exp(upper[a, b]))
    league[[length(league) + 1]] <- row
  }
  small_values <- spec_value("small_values", "desirable")
  ranking <- tryCatch(netmeta::netrank(net, small.values = small_values), error = function(e) {
    netmeta::netrank(net, small.values = if (small_values == "desirable") "good" else "bad")
  })
  scores <- if (random) ranking$ranking.random else ranking$ranking.common
  if (is.null(scores)) scores <- if (random) ranking$Pscore.random else ranking$Pscore.common
  results$model <<- list(measure = measure, random = random, reference = reference, tau = net$tau,
                        studies = net$k, treatments = net$n, pairwise_comparisons = net$m, designs = net$d)
  results$heterogeneity <<- list(Q = net$Q, df = net$df.Q, p_value = net$pval.Q, I2 = net$I2, tau2 = net$tau2)
  results$league <<- league
  results$p_scores <<- lapply(names(scores), function(name) list(treatment = name, p_score = as.numeric(scores[[name]])))
  results$node_splitting <<- tryCatch({
    split <- netmeta::netsplit(net)
    direct <- if (random) split$direct.random else split$direct.common
    indirect <- if (random) split$indirect.random else split$indirect.common
    compare <- if (random) split$compare.random else split$compare.common
    lapply(seq_along(split$comparison), function(i) list(
      comparison = split$comparison[i], direct = direct$TE[i], indirect = indirect$TE[i],
      difference = compare$TE[i], p_value = compare$p[i]
    ))
  }, error = function(e) list(error = conditionMessage(e)))
  results$inconsistency <<- tryCatch({
    decomposition <- netmeta::decomp.design(net)
    q <- decomposition$Q.decomp
    list(
      decomposition = lapply(seq_len(nrow(q)), function(i) list(source = rownames(q)[i], Q = q$Q[i], df = q$df[i], p_value = q$pval[i])),
      design_by_treatment = if (is.null(decomposition$Q.inc.random)) NULL else list(
        Q = decomposition$Q.inc.random$Q, df = decomposition$Q.inc.random$df, p_value = decomposition$Q.inc.random$pval
      )
    )
  }, error = function(e) list(error = conditionMessage(e)))
  save_plot("network", function() netmeta::netgraph(net, number.of.studies = TRUE), width = 8, height = 8)
  save_plot("forest", function() {
    meta::forest(net, reference.group = if (nzchar(reference)) reference else treatments[1], sortvar = TE)
  }, width = 9, height = max(4, 0.4 * length(treatments) + 2))
})
