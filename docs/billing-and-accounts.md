# Plans, billing, and your account

## Paying for AI

AI you run on your own provider key is never charged by OmniReview: your provider bills you directly, and you can
choose under Settings to use only your own keys.

AI run on OmniReview's keys is paid from your **AI balance**, a prepaid amount you top up under Billing. Each engine
has a published price per million tokens, listed on the [pricing page](../pricing): it is four times what the provider
charges us, so cheaper engines stay cheaper for you and the choice of engine is yours. A screening run of a few
thousand records on a small model costs cents; a long manuscript draft on a premium model costs more.

- Every AI task shows which engine it used, and each charge appears on the Billing page with the run that caused it.
- When the balance runs out, AI on OmniReview's keys stops with a message, and everything else keeps working. Top up,
  or add your own provider key, and it resumes.
- Invoiced customers can have credit added by an administrator instead of paying online.
- A model with no price set can only be used with your own key.
- Prices follow what the providers charge. When a provider changes its prices, ours change with them.

## Plans and limits

Each billing account has a plan: your personal account, or an organization you belong to. A project counts against
its organization when it was created in one, and against its creator otherwise.

Plans limit:

- **Projects** you can own.
- **Members in a project.** Invitations count when they are sent.
- **Records added each month** by searches and imports.
- **Storage** is included per plan; files you keep in your own bucket don't count (see below).
- **Stored documents** (full texts, uploads, and plots), in MB. Files kept in your own storage bucket don't count; see
  [Your own AI keys and your own storage](your-keys-and-storage.md).
- **Active surveillance schedules** for living reviews.
- **Analysis compute** each month, in minutes of R run time.
- **API access** with personal tokens, and **webhooks**, which are on some plans only.

When an action would go over a limit, OmniReview says which limit and links to Billing. Monthly allowances reset on
the first day of the month.

## Changing plan

Open **Billing** from the dashboard or Settings. Choose a plan and pay monthly or yearly through the payment page. The
new limits apply as soon as the payment is confirmed. Use **Manage billing** to update your card, see invoices, or
change plan. **Cancel** keeps the plan until the end of the paid period, then returns the account to Free; nothing is
deleted, but you can't add beyond the Free plan's limits.

If a payment fails, the plan keeps working while you update your payment details, and you get a notification.

Organization plans (for example an institution invoiced yearly) are set up with us. Organization owners and admins see
the organization's plan and usage in Billing.

## Security

Under **Settings → Security**:

- **Change your password.** Other signed-in sessions end.
- **Two-factor sign-in.** Scan or paste the setup key into an authenticator app (Google Authenticator, 1Password,
  Authy, and similar), confirm a code, and keep the recovery codes somewhere safe. Each recovery code works once. If
  you lose both your device and your codes, contact support.
- **Forgot your password?** Use the link on the sign-in page. The reset link works once, for an hour.

New accounts confirm their email address through a link. If a link expires, sign in (or use Settings) to get a new
one.

## Your data

Under **Settings → Privacy**:

- **Download your data** as JSON: your profile, projects and roles, decisions, comments, tasks, notifications, and
  token names. Passwords, keys, and token values are never included.
- **Delete your account.** Deletion happens 14 days after you ask, and you can cancel until then. If you are the only
  owner of a project other people work on, hand over ownership first. Projects only you belonged to are deleted with
  their files. Your contributions to shared reviews stay part of those reviews, shown as "Deleted user", because a
  review's audit trail must stay complete.

The Terms of Service, Privacy Policy, and related documents are linked at the bottom of every page.
