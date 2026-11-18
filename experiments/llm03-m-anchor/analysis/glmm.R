# Step 2 of the analysis (analysis-plan-v1.md, sections 3, 4 and 6.5-6.6): GLMM (logit) on attack episodes,
# model-averaged contrasts, Holm over the three primary tests, pre-specified fallbacks, sensitivity and
# exploratory contrasts, interaction tests and variance components.
#
# usage: Rscript analysis/glmm.R <out_dir>      (reads <out_dir>/episodes.csv written by prep.py)

suppressPackageStartupMessages({ library(lme4); library(jsonlite) })
args <- commandArgs(trailingOnly = TRUE)
out_dir <- args[1]
d <- read.csv(file.path(out_dir, "episodes.csv"), stringsAsFactors = FALSE)
atk <- subset(d, label == "attack")
atk$condition <- factor(atk$condition, levels = c("C3", "C1", "GC", "C4"))
atk$sender <- factor(atk$sender)
senders <- levels(atk$sender)

ctrl <- glmerControl(optimizer = "bobyqa", optCtrl = list(maxfun = 2e5))
f_full <- function(y) as.formula(paste(y, "~ condition * sender + (1 | case_type) + (1 | case_type:case_id) + (1 | trial_id)"))
f_step2 <- function(y) as.formula(paste(y, "~ condition * sender + (1 | case_type:case_id) + (1 | trial_id)"))

converged <- function(fit, warns) {
  msgs <- c(warns, fit@optinfo$conv$lme4$messages)
  bad <- grepl("failed to converge|unable to evaluate scaled gradient|Model is nearly unidentifiable|degenerate", msgs)
  !any(bad)
}

fit_glmm <- function(y, formula_fun) {
  warns <- character(0)
  fit <- withCallingHandlers(
    tryCatch(glmer(formula_fun(y), data = atk, family = binomial, nAGQ = 1, control = ctrl), error = function(e) e),
    warning = function(w) { warns <<- c(warns, conditionMessage(w)); invokeRestart("muffleWarning") })
  if (inherits(fit, "error")) return(list(ok = FALSE, fit = NULL, warnings = c(warns, conditionMessage(fit))))
  list(ok = converged(fit, warns), fit = fit, warnings = c(warns, fit@optinfo$conv$lme4$messages),
       singular = isSingular(fit))
}

# Plan 3.3: try the full model, then without (1 | case_type).
fit_with_fallback <- function(y) {
  a <- fit_glmm(y, f_full)
  if (a$ok) return(c(a, step = "1_full"))
  b <- fit_glmm(y, f_step2)
  if (b$ok) return(c(b, step = "2_without_case_type", step1_warnings = list(a$warnings)))
  list(ok = FALSE, fit = NULL, step = "failed", warnings = c(a$warnings, b$warnings))
}

grid <- expand.grid(condition = levels(atk$condition), sender = senders)
xrow <- function(fit, cond, snd) {
  X <- model.matrix(~ condition * sender, data = transform(grid, condition = factor(condition, levels(atk$condition)),
                                                           sender = factor(sender, senders)))
  X <- X[, names(fixef(fit)), drop = FALSE]
  X[grid$condition == cond & grid$sender == snd, ]
}

# contrast of condition b against a (b - a on the log-odds scale); snd = NULL -> average over the senders
contrast <- function(fit, a, b, snd = NULL) {
  use <- if (is.null(snd)) senders else snd
  L <- Reduce(`+`, lapply(use, function(s) xrow(fit, b, s) - xrow(fit, a, s))) / length(use)
  est <- sum(L * fixef(fit)); se <- sqrt(as.numeric(t(L) %*% as.matrix(vcov(fit)) %*% L))
  z <- est / se
  list(method = "glmm_wald", log_or = est, se = se, or = exp(est), or_ci95 = exp(est + c(-1, 1) * qnorm(0.975) * se),
       z = z, p = 2 * pnorm(-abs(z)))
}

# Plan 3.3 step 3: separation in the two compared conditions for any sender (or for one sender)
separated <- function(y, a, b, snd = NULL) {
  s <- subset(atk, condition %in% c(a, b) & (is.null(snd) | sender %in% snd))
  m <- tapply(s[[y]], list(s$condition, s$sender), mean)[c(a, b), , drop = FALSE]
  any(m == 0 | m == 1, na.rm = TRUE)
}

# Exact conditional test on pairs that share one initial evaluation, stratified by sender
mcnemar_exact <- function(y, a, b, snd = NULL) {
  s <- subset(atk, condition %in% c(a, b) & (is.null(snd) | sender %in% snd))
  w <- reshape(s[, c("trial_id", "condition", y)], idvar = "trial_id", timevar = "condition", direction = "wide")
  ya <- w[[paste0(y, ".", a)]]; yb <- w[[paste0(y, ".", b)]]
  n10 <- sum(ya == 1 & yb == 0); n01 <- sum(ya == 0 & yb == 1)
  p <- if (n10 + n01 == 0) 1 else binom.test(n01, n10 + n01, 0.5)$p.value
  list(method = "stratified_exact_mcnemar", pairs = nrow(w), a_only = n10, b_only = n01,
       conditional_or_b_vs_a = if (n10 > 0) n01 / n10 else NA, p = p)
}

fits <- list()
get_fit <- function(y) { if (is.null(fits[[y]])) fits[[y]] <<- fit_with_fallback(y); fits[[y]] }

test <- function(y, a, b, snd = NULL) {
  # main tests: plan 3.3 step 3. Per-sender contrasts (exploratory) use the same exact test within the sender.
  if (separated(y, a, b, snd)) return(c(mcnemar_exact(y, a, b, snd), reason = "separation"))
  ft <- get_fit(y)
  if (!ft$ok) {
    return(c(mcnemar_exact(y, a, b, snd), reason = "glmm_not_converged"))
  }
  c(contrast(ft$fit, a, b, snd), glmm_step = ft$step)
}

rates <- function(y, conds) {
  setNames(lapply(senders, function(s) setNames(lapply(conds, function(cn)
    mean(atk[atk$condition == cn & atk$sender == s, y])), conds)), senders)
}

primary_spec <- list(
  list(id = "H4a", outcome = "mis_propagation", a = "C3", b = "C4"),
  list(id = "H4b", outcome = "attempt", a = "C3", b = "C4"),
  list(id = "H4prime", outcome = "mis_propagation", a = "GC", b = "C4"))

primary <- lapply(primary_spec, function(p) c(p, test(p$outcome, p$a, p$b)))
holm <- p.adjust(sapply(primary, `[[`, "p"), method = "holm")
for (i in seq_along(primary)) { primary[[i]]$p_holm <- holm[[i]]; primary[[i]]$significant_holm <- holm[[i]] < 0.05 }

per_sender <- list()
for (p in primary_spec) for (s in senders)
  per_sender[[length(per_sender) + 1]] <- c(p, sender = s, test(p$outcome, p$a, p$b, s))

sensitivity <- lapply(list(list(id = "H4a_not_sent_counted", a = "C3", b = "C4"),
                           list(id = "H4prime_not_sent_counted", a = "GC", b = "C4")),
                      function(p) c(p, outcome = "mis_propagation_if_not_sent_counted",
                                    test("mis_propagation_if_not_sent_counted", p$a, p$b)))
for (i in seq_along(sensitivity)) {
  prim <- primary[[c(1, 3)[i]]]
  sensitivity[[i]]$same_conclusion_as_primary <-
    ((sensitivity[[i]]$p < 0.05) == (prim$p < 0.05)) &&
    (sign(if (!is.null(sensitivity[[i]]$log_or)) sensitivity[[i]]$log_or else log(sensitivity[[i]]$conditional_or_b_vs_a)) ==
     sign(if (!is.null(prim$log_or)) prim$log_or else log(prim$conditional_or_b_vs_a)))
}

exploratory <- list(
  c(id = "C1_vs_C3_mis_propagation", outcome = "mis_propagation", test("mis_propagation", "C3", "C1")),
  c(id = "C1_vs_C3_attempt", outcome = "attempt", test("attempt", "C3", "C1")),
  c(id = "GC_vs_C4_attempt", outcome = "attempt", test("attempt", "GC", "C4")),
  c(id = "receiver_false_approval_C3_vs_C4", outcome = "receiver_false_approval", test("receiver_false_approval", "C3", "C4")),
  c(id = "receiver_false_approval_GC_vs_C4", outcome = "receiver_false_approval", test("receiver_false_approval", "GC", "C4")))

interaction <- lapply(c("mis_propagation", "attempt"), function(y) {
  ft <- get_fit(y)
  if (!ft$ok) return(list(outcome = y, method = "not_estimable"))
  add <- update(ft$fit, . ~ . - condition:sender)
  a <- anova(add, ft$fit)
  list(outcome = y, method = "lrt_condition_x_sender", chisq = a$Chisq[2], df = a$Df[2], p = a$`Pr(>Chisq)`[2])
})

varcomp <- lapply(names(fits), function(y) {
  ft <- fits[[y]]
  if (!ft$ok) return(list(outcome = y, method = "not_estimable"))
  vc <- as.data.frame(VarCorr(ft$fit))
  ci_warn <- character(0)
  ci <- withCallingHandlers(
    tryCatch(confint(ft$fit, parm = "theta_", method = "profile", oldNames = FALSE), error = function(e) conditionMessage(e)),
    warning = function(w) { ci_warn <<- c(ci_warn, conditionMessage(w)); invokeRestart("muffleWarning") })
  list(outcome = y, glmm_step = ft$step, singular = ft$singular, sd = setNames(as.list(vc$sdcor), vc$grp),
       profile_ci95_sd = if (is.character(ci)) ci else lapply(seq_len(nrow(ci)), function(i) list(parm = rownames(ci)[i], lo = ci[i, 1], hi = ci[i, 2])),
       profile_warnings = ci_warn, warnings = ft$warnings)
})

out <- list(
  software = list(R = R.version.string, lme4 = as.character(packageVersion("lme4")),
                  Matrix = as.character(packageVersion("Matrix"))),
  n_attack_episodes = nrow(atk), senders = senders,
  observed_rates = list(mis_propagation = rates("mis_propagation", levels(atk$condition)),
                        attempt = rates("attempt", levels(atk$condition))),
  primary = primary, per_sender = per_sender, sensitivity = sensitivity, exploratory = exploratory,
  interaction = interaction, variance_components = varcomp,
  note = "Contrasts are b - a on the log-odds scale (or < 1 means b is lower). Model-averaged contrasts weight the two senders equally.")
writeLines(toJSON(out, auto_unbox = TRUE, pretty = TRUE, digits = NA, na = "null"), file.path(out_dir, "glmm_results.json"))
cat("written", file.path(out_dir, "glmm_results.json"), "\n")
