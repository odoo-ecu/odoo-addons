# User Access Studio CHANGELOG

Module: `eh_access_studio`. License LGPL-3.0 or later.

## v19.0.3.0.0 (September 2026)

Guided policy setup, access inspection, and governed role assignments.

- **Build an inactive policy draft.** Templates validate the installed target
  model and expose company scope. The interface picker discovers named controls
  from installed views. The guided record filter combines owner/team scopes,
  runtime placeholders, and Odoo's visual domain editor.
- **Inspect a user's current access.** Choose a model, operation, company, and
  optional record to check native Odoo access and Studio restrictions. The result
  explains contributing groups, rules, and profiles. It performs no business
  create, write, or delete; proposed values and custom workflow execution remain
  outside the check.
- **Manage roles and requests.** Roles combine native groups, included roles,
  and company-scoped restriction profiles. Employees can request access and
  another manager can approve it. Assignment history, reasons, provenance, and
  an ownership ledger support revocation without removing overlapping or
  independently held memberships. Native groups remain database-wide.
- **Schedule temporary grants.** Assignments support start and end times, with
  reconciliation scheduled every minute. Effective removal depends on a healthy
  scheduler; an end time is not an exact-time authorization cutoff. Profile
  expiry removes a restriction and is not a substitute for expiring a grant.
- **Review policy imports and revisions.** YAML schema 2 previews differences,
  validates references, detects changes after preview, and requires confirmation
  for active-policy changes. Imports and restores create revision snapshots;
  ordinary form edits require the explicit Save Revision action. Revisions and
  governance events are immutable through the application, not against database
  administrators.
- **Generate an optional AI draft.** A system administrator can configure the
  OpenAI provider. Managers explicitly consent to send a prompt and selected
  model field schema, review structured output, then create an inactive profile
  separately. AI does not assign users or activate the profile. Provider tests
  use mocks; a live provider call is not claimed as release validation. Provider
  charges are separate from this free LGPL module. The created revision retains
  provider/model identifiers, generation time, prompt and draft digests, and the
  validation schema version, without retaining the raw prompt.
- **See operational follow-up in Overview.** Managers can review pending
  requests, assignments approaching expiry, overdue reconciliation, and recent
  governance evidence. These are operational counts, not a security risk score.

The test suite covers these workflows and their authorization boundaries.
Historical test totals below describe earlier releases, not the validation
result for this release.

## v19.0.2.12.0 (September 2026)

Round eighteen of live adversarial probing.

- **Hiding a field left the value printed under another name.** Hiding
  the parent a contact is filed under still spelled the parent out in
  every list, breadcrumb and autocomplete through display_name and
  complete_name. The printed name now follows the fields that form it,
  and name_search and its web variant answer with the same stripping and
  blanking as a read, so a display-enriching context key cannot put a
  hidden value back into the string.
- **A hidden server action was only hidden.** It disappeared from the
  Actions menu and still ran over RPC by id. It is refused at run(),
  which also closes the route where a stock "Schedule Activity" action
  scheduled on a model whose profile refuses activities: an action a
  person runs is that person's call.
- A negated condition on a hidden field now matches everything rather
  than nothing, so an ordinary filter no longer answers with an empty
  list.
- The sign-in shortcut on Odoo 16 to 18 is re-read every minute rather
  than every day, so a block takes effect for API callers on every
  worker.
- The Disable Login help, and the listing, now say that an administrator
  is never refused.

Also in this release, from an independent review of the previous round:

- **Hidden view types were inert on Odoo 16 and 17**, which load an
  action through read() rather than the method 18 and 19 use. Both paths
  are filtered now.
- **One hidden field blanked far too much.** A computed field was
  followed whenever it shared a single dependency, so hiding a
  document's status blanked every amount on it. A computed field is now
  followed only when everything it is computed from, beyond the record's
  own name, is hidden.
- **A many2one still printed a hidden name**, because the label is built
  by the other model under superuser. The label follows that model's
  rule now.
- More chatter paths judged as the person's own: sharing a record,
  inviting a follower, the mail gateway entry points on any model, a
  notification that names the record it is sent from, and an attachment
  created directly rather than through the upload route.

223 in-database tests (including a browser tour and an HTTP route test)
plus 18 standalone, green on Odoo 16, 17, 18 and 19.

## v19.0.2.11.0 (September 2026)

Round seventeen of live adversarial probing, and a second independent
review of the previous round's changes.

- **An aggregate answered what a hidden field holds.** A HAVING
  condition or a sort written as "value:sum" was never recognised as
  naming the field, so grouping decided which records came back by the
  hidden value and a dozen calls found it exactly. Every domain and sort
  expression is now read down to the field it names.
- **Odoo 16 grouped without any of that.** That series groups through
  read_group alone, where only the labels were blanked, so the domain,
  the sort and the grouping terms went through untouched. They are
  judged there now, as on the other series.
- **Conditional rules are honoured where they were not**: a rule scoped
  with "Only when" no longer strips the field from every create, no
  longer demands it on every record, and no longer blocks a whole export
  column; the covered cells are blanked row by row instead.
- **Hiding a record's name now hides the name it is displayed under**,
  so the list, every dropdown and name search stop showing it.
- **An existing note could be rewritten** through an x2many command on
  the document; the edit is judged as the kind of message it is.
- **A blank served for a hidden field is never written back**, and where
  a rule is conditional the clear is applied to the records it does not
  cover instead of being discarded.
- Derivation follows related fields all the way and a computed field one
  hop, and only where the user cannot write it, so one hidden field no
  longer blanks a quarter of a large model.
- More chatter paths judged as the person's own (the invoice send
  dialog and other sending wizards, mail.thread.message_process), and
  fewer judged wrongly: a framework message about another record is no
  longer refused, and the chatter waiver no longer stands down the rules
  on the chatter's own models.
- A filter naming a field the model does not have is refused while the
  administrator is looking at it, instead of breaking every read later.
- An administrator is never locked out by a crash inside the domain
  gate; the keyword form of onchange meets the same gate as the
  positional one; the archived-records filter survives the search
  rewrite; the session publishes the interface flags to every user, so
  the chatter never waits for a round trip.

217 in-database tests (including a browser tour and an HTTP route test)
plus 18 standalone, green on Odoo 16, 17, 18 and 19.

## v19.0.2.10.0 (September 2026)

Round sixteen of live adversarial probing, plus an independent review of
round fifteen's own changes.

- **CRITICAL, and a defect of round fifteen's making: a hidden value
  could be destroyed.** Following computed dependency paths across
  models pulled in fields that are WRITABLE (an employee's work email,
  a lead's email), and the blank served on read was saved back over the
  real value, through inverses core runs as superuser. Derivation now
  follows related fields only, which are true mirrors; a blank this
  module served is never written back; an onchange answer leaves the
  key out instead of blanking it; and a field hidden by derivation is
  taken off the form rather than shown empty and editable.
- **CRITICAL route bypass: one extra character.** Odoo routes ignore a
  trailing slash, so /mail/message/post/ walked past the chatter rules
  that /mail/message/post enforces, and /mail/attachment/upload/ past
  the attachment rule. Routes are now matched as Odoo dispatched them.
  An HTTP test posts through three spellings of the route.
- **Business flows are no longer refused.** A post made while a flow
  runs (a picking's SMS confirmation, a stage change, a refusal note)
  is the flow's message, not the person's, and is not judged. Only what
  a person writes through the chatter, the composer, a template, a
  scheduled message or a direct call is.
- **A menu could open a blank new record.** Hiding one view type left
  the form in front of the action; a surviving list-type view now leads.
- **The manager hierarchy placeholders were dead code.**
  __subordinate_uids__ and __team_uids__ called a method that did not
  exist, so every rule using them failed closed and showed nothing.
  The chain is now read from the HR app, breadth first and loop safe;
  without HR it is empty, as documented.
- **Sign-in on a dated profile** is no longer refused by the timezone
  lock: core writes the browser timezone during sign-in, outside any
  call the person made.
- The search menu switches are applied with one stylesheet on every
  series instead of two template patches, which also covers Odoo 19's
  second custom group by entry; the chatter switches travel with the
  session, so the chatter no longer waits for a round trip on every form
  open; grouping is only collapsed for a person's own read, so server
  code keeps real totals; a conditional rule reads only the fields it
  names; the record-rule lookup is cached like the others.

208 in-database tests (including a browser tour and an HTTP route test)
plus 18 standalone, green on Odoo 16, 17, 18 and 19.

## v19.0.2.9.0 (September 2026)

Round fifteen of live adversarial probing, and the round that found the
module had never been tested through a browser.

- **CRITICAL, introduced in 19.0.2.8.0 and fixed here: every list and
  kanban view failed.** The new web_read / web_search_read / web_save
  overrides, and the older read_group one, did not carry the decorators
  core gives those methods, so call_kw read the domain as record ids.
  Overrides now inherit core's dispatch markers automatically at
  registry load, a test asserts that parity on every model, and a
  browser test walks the real screens on each series.
- **Sign-in was broken on Odoo 16, 17 and 18.** _login is a classmethod
  there, with arguments that changed twice, so the override raised a
  TypeError for every sign-in. Each series now gets the shape core
  calls, with a test that signs in.
- **The web client could not load at all on Odoo 16**, and the chatter
  patch pointed at the wrong module on Odoo 17: both series draw the
  chatter differently. Odoo 16 now publishes the profile's interface
  switches as body classes with a stylesheet, and 17 gets the module
  path and property name it uses.
- **An unconditional lock is no longer narrowed** by a conditional
  hidden rule on the same field.
- **A conditionally hidden field** is blanked only on the records the
  condition covers, and is scrubbed from searches and sorts on all of
  them, so a search cannot answer what a read withholds.
- **A hidden field no longer leaks through computed fields on other
  models** that depend on it through a path, such as a lead's email
  computed from the contact's.
- **An onchange answer no longer tests hidden values**: a candidate VAT
  number can no longer be probed through the keys that come back.
- **More chatter paths judged as the person's own**: mail.template
  send_mail, activity_send_mail and the other mail-sending methods, a
  subtype flipped to Note after posting, and an import through
  base_import. Files cannot be dropped on a chatter that is hidden
  entirely.
- **The chatter's own Send and Log buttons work on read-only records
  again**: the route asks for write access before posting, which is now
  covered by the same narrow waiver as posting itself.
- A profile's node rules combine with the view's own conditions instead
  of replacing them, search filter rules are hidden outright rather than
  stamped with a record condition that the client cannot evaluate, and
  the sign-in response carries no developer mode.

204 in-database tests (including a browser tour) plus 18 standalone,
green on Odoo 16, 17, 18 and 19.

## v19.0.2.8.0 (September 2026)

Round fourteen of live adversarial probing.

- **A hidden field showed through a related record's display name**
  when a read specification asked for show_email on it, because core
  resolves that name as superuser. Display-enriching context keys are
  stripped from read specifications and from reads of related records
  whenever the related model has a hidden field.
- **Delegated and related copies of a hidden field.** A related field
  that mirrors a field hidden on another model is hidden too, so
  email_normalized and email_formatted on a user follow the contact's
  rule. Searching is scrubbed along the whole path and inside
  sub-domains on related models, so res.users email and
  partner_id.email are no longer a prefix oracle.
- **The SMS composer and the composers' kept logs** answer to the
  chatter rules, and an SMS posted as the user is a post like a comment.
- **An activity moved by a command** onto another record is judged on
  the model it lands on, and the feedback text of "done and schedule
  next" answers to Hide Log Note.
- **Own preferences through any related field** (with HR, the linked
  employee's self-editable fields) answer to the target model's locks
  and to Read-Only Mode.
- **A personal default for a locked field** is refused, so default_get
  cannot fill a value the user may not choose.
- A profile's view modifiers are combined with the view's own instead
  of replacing them, and a label follows its field's condition; saving
  the archive flag unchanged is not archiving; the blank group a hidden
  grouping collapses into opens on the records it counts; the sign-in
  response carries no developer mode for a profile that disables it.

189 in-database tests plus 18 standalone, green on Odoo 16, 17, 18
and 19.

## v19.0.2.7.0 (September 2026)

Round thirteen of live adversarial probing.

- **Chatter entries made without message_post.** message_ids and
  activity_ids commands in a record's own write or create are the
  caller's post or scheduling; "send later" (mail.scheduled.message) is
  judged when the message is queued; the mass-mail composer and a direct
  mail.message create answer to the same rules; message_notify answers
  to Hide Send Message; a meeting linked to a record counts as
  scheduling an activity; and the free text added when marking an
  activity done answers to Hide Log Note. Creating a mail.mail on a
  record (it carries a message by delegation) and editing a posted
  message answer to the same rules. A note is now any post on an
  internal subtype, not only mail.mt_note.
- **A user could move their own "today"** by changing their timezone
  and step out of a dated profile early. While a dated profile applies,
  only an administrator changes that user's timezone.
- **API sign-in cached by core** (res.users.check on 16 to 18,
  _check_uid_passwd on 19) met no sign-in block that started later. The
  date-keyed block lookup is asked on every API call.
- **User administrators could not duplicate users**: the profile link
  is no longer copied.
- **Hidden-field scrubbing blinded business code.** Search conditions
  are scrubbed while the outermost call is a search-shaped read (or no
  RPC call is in flight), so find_or_create and similar code running as
  the user match contacts again instead of creating duplicates.
- A database constraint on create names only the locked field that
  failed; the phone layout never opens on a hidden view type; the
  browser cache key also follows the user's allowed companies; on
  Odoo 18 and 19 check_access_rights no longer repeats the gate.

179 in-database tests plus 18 standalone, green on Odoo 16, 17, 18
and 19.

## v19.0.2.6.0 (September 2026)

Round twelve of live adversarial probing.

- **The chatter, activity, copy_data and Duplicate gates depended on the
  transport.** They recognised a person's direct call by the web
  client's request parameters, so XML-RPC, /jsonrpc and Odoo 19's
  /json/2 all looked like business flows. The outermost RPC call is now
  recorded where every transport ends (call_kw, and /json/2's method
  lookup), and the gates read that. message_post_with_source and the
  other public message_post variants count as direct posts, a direct
  mail.message create answers to Send Message and Log Note, and Discuss
  channels are never silenced by the chatter toggles.
- **A conditional lock could be walked past through an inverse field**
  (company_type sets is_company, including from the contact form). A
  conditionally locked field is now also judged after the write, on the
  record the ORM produced, and the write rolls back if the condition
  holds there.
- **name_search and display_name searches reached a hidden field**
  through _rec_names_search after the domain was scrubbed. The
  expansion is now scrubbed too.
- **Grouping by a hidden field revealed how many distinct values there
  were.** The hidden term is taken out of the grouping and a blank put
  back, on _read_group and on Odoo 19's grouping sets used by the pivot.
- **A hidden contact field was readable on res.users**, which carries
  contact fields by delegation. Rules on the parent model now govern
  delegated copies. Read-Only Mode covers contact data a user edits
  through their preferences; language, timezone and picture stay theirs.
- **Both ends of a relation**: a many2many lock, and Hide Edit, hold when
  the relation is written from the other model, and removing a child
  from a one2many answers to the lock on its parent field.
- **Failure posture**: field locks and hidden-field blanking now fail
  closed for users named on a profile (they used to switch off); the
  sign-in gate, the open-session check and the developer-mode strip
  refuse only listed users when they fail, never users on no profile or
  administrators. The developer-mode strip reads the session's user.
- An action whose list-type views are all hidden is left whole rather
  than reduced to a blank new-record form.

167 in-database tests plus 18 standalone, green on Odoo 16, 17, 18
and 19.

## v19.0.2.5.0 (September 2026)

Round eleven of live adversarial probing.

- **Send Message and Log Note were bypassed on the chatter's own
  route.** From Odoo 18, /mail/message/post checks the caller and then
  posts as superuser, which every gate stood aside for. On the post
  routes, a direct message_post call and the full composer, the person
  who made the request is now judged, whatever message_type the payload
  names. A post that names no subtype is a note, as core files it.
- **Hide Schedule Activity never ran.** mail.activity.mixin defines
  activity_schedule without calling super, so the override on
  mail.thread was dead code. The gate now sits on mail.activity create,
  and on a write that moves an activity, and judges the person when the
  call is theirs: the activity form, the schedule wizard or
  activity_schedule. Activities a business flow creates on its own are
  not blocked.
- **copy_data over RPC returned hidden fields.** Called directly it is a
  read, so hidden fields are blanked in its answer, lines included.
  Business code duplicating a record keeps its values.
- **A conditional lock could be walked past in one write on a
  contact.** Core writes is_company as superuser before the rest, so the
  rest was judged against the new value. The whole payload is now
  checked before the split. A user's own preferences, which core also
  writes as superuser, answer to field locks, including locks on the
  linked contact.
- **One failed profile lookup locked out users under no profile**,
  including portal and public visitors. A one-query membership check now
  settles it: only users named on a profile fail closed.
- **The Odoo 19 web client showed the previous screens after a profile
  edit**, from the copy it keeps in the browser. The browser cache key
  now carries the configuration stamp for users under a profile.
- Odoo 17 asked for the rule domain under sudo while flushing a search,
  and resolving profiles there recursed. The rule extension now stands
  aside for the superuser and for its own tables before any lookup.

151 in-database tests plus 18 standalone, green on Odoo 16, 17, 18
and 19.

## v19.0.2.4.0 (September 2026)

Round ten of live adversarial probing.

- **copy(default) could write a locked field on another record.** Copy
  runs with the field gate stood down so the source's own locked values
  carry over; nested commands in the caller's default rode through that
  window. They are now checked as the real user before it opens, and
  linking existing records into a one2many (4 / 6 commands) counts as
  editing them.
- **Hide Duplicate broke credit notes, reversals and backorders**, which
  copy records from inside business methods. It now gates the user's
  own Duplicate only; record creation stays governed by Hide Create.
- **The listing said a read-only profile cannot post to the chatter.**
  It can, deliberately, since round six. Corrected.
- **Hide Send Message, Log Note and Activities are enforced on the
  server**, not only by removing the buttons. Framework notifications
  are never blocked.
- **Discuss works under Read-Only Mode**, on the discuss.* models from
  Odoo 17 and mail.channel before it.
- **An attachment that stores a binary field** (res_field set) is that
  field: it answers to the field lock and gets no chatter waiver.
- export_data no longer recurses when a display context key is set; the
  sign-in check's second copy fails as a clean refusal.

141 in-database tests plus 18 standalone, green on Odoo 16, 17, 18
and 19.

## v19.0.2.3.0 (September 2026)

Round nine of live adversarial probing.

- **Archiving a child through its parent** ((1, id, {active: False}) or
  (0, 0, {active: False}) on a one2many) skipped restrict_archive,
  because core flushes those children as superuser. Checked up front.
- **A conditional field rule stored after an unconditional one on the
  same field unlocked it.** The unconditional line now wins whatever
  order the lines are in.
- **show_email put a hidden email back into display_name.** Display
  enriching context keys are dropped for a user with a hidden field on
  that model, in read and in export.
- **res.partner writes is_company as superuser before the rest**, which
  skipped field locks on it. A res.partner guard checks it first.
- **restrict_delete is delete only.** It blocks the unlink that core
  converts into an archive on some models, and no longer blocks the
  Archive button.
- **Time windows follow the users' dates, not UTC.** The job runs
  hourly and invalidates the caches around any window boundary within a
  day of now, and a profile is archived only once its last local day is
  over everywhere. Existing installs are moved to the hourly schedule
  on upgrade unless an administrator changed it.
- The report gate fails closed on a lookup error, the administrator
  exemption reads group membership as superuser, and a crash in the
  sign-in gate is a clean refusal instead of a server error.
- Attaching a file works under Read-Only Mode; sorting by a hidden
  field is ignored; the date lookup on the hot path is memoised.

135 in-database tests plus 18 standalone, green on Odoo 16, 17, 18
and 19.

## v19.0.2.2.0 (September 2026)

Round eight of live adversarial probing: seven high-severity findings.

- **Posting a message could rewrite records.** The chatter waiver
  covered everything that ran during a post, so a read-only user could
  change data through a post hook (crm writes partner_id onto every
  open lead sharing an email). It now covers only the write ACCESS
  CHECK core makes on the document; a real write to that model during
  the post is gated as usual, and the field gate stays on throughout.
- **Children created through their parent skipped every gate.** Core
  flushes res.partner child_ids as superuser. Nested one2many and
  many2many commands are now checked as the real user before they go
  down, and locked child fields are stripped from them.
- **A hidden field leaked through fields derived from it and through
  search.** email_normalized, email_formatted and related fields carried
  the same value; ilike searches could reconstruct it. Read, grouping
  and export now use the closure of every field related through or
  computed from a hidden one, and search conditions on those fields
  match nothing. The record's name and display fields are never hidden
  by derivation.
- **The create-time strip over-reached.** A locked many2many with a
  default made the model uncreatable, and copy() with any default reset
  every locked field. Only fields the caller supplied, or smuggled
  through a default_<field> context key, are touched now.
- **Hidden reports still rendered through the portal**, which renders
  as superuser. The gate resolves the real requester, fails closed on a
  lookup error, and matches sibling actions sharing the same template.
- **A disabled user kept access through API keys and open sessions.**
  API-key authentication is gated, and a session that was open when
  the block was switched on is ended on its next request.
- **get_views no longer prunes view types.** An action returned as a
  Python dict still advertised the mode and the client then asked for a
  view the server refused. Hidden view types are removed from stored
  actions only, which the listing documents.
- A conditional lock is judged against the record before AND after the
  write, closing a two-step unlock; a many2many rewritten to its
  current value is no longer a change; delete-as-archive reports the
  right operation; a constraint violation is blamed on the profile only
  when it concerns a locked field.

127 in-database tests plus 18 standalone, green on Odoo 16, 17, 18
and 19.

## v19.0.2.1.0 (September 2026)

Round seven of live adversarial probing.

- **Soft restriction plus "No access" disclosed the whole model.** The
  soft-restrict skip ran before the read right was checked, so a rule
  granting nothing dropped its own block on read. It now abstains only
  when it actually grants read.
- **The company switcher was a way out of a company-scoped profile.**
  Profiles now apply to a user who can enter ANY of their companies,
  resolved once in `_get_active_profile_ids` so every gate agrees.
- **A failed profile lookup could write-lock the entire database.**
  Failing closed is right for restricted users; administrators are now
  always exempt, so recovery never needs a shell.
- **Record-rule date placeholders honoured a forged timezone.** They
  now use the timezone stored on the user, like the profile window.
- **Locking a field that the database requires** (a SQL CHECK or NOT
  NULL, not `required=True`) ended in a raw constraint violation. It is
  now explained, with the message built before the insert, because an
  aborted transaction cannot run the translation lookup afterwards.
- **Group-by drill-down carried the hidden value** in its `__domain`.
  Blanked groups no longer expose one.
- **Hidden reports were matched on the report's model only**, while the
  configuration offers them by the model they are bound to. Both are
  checked.
- The last three direct reads of `env.company` in the profile model are
  gone; every lookup uses the forge-proof resolver.

118 in-database tests plus 18 standalone, green on Odoo 16, 17, 18
and 19.

## v19.0.2.0.0 (September 2026)

Capability release, plus the bypasses an independent probe proved on
the running code.

### Added, the capabilities that used to need a second paid app
- **Conditional rules.** Field, button, tab and kanban-link rules take
  an "Only when" expression over the record's own fields, in the same
  form Odoo writes view modifiers. On Odoo 17 and later the condition
  is handed to the client and evaluated per record, and the SAME
  expression is re-evaluated on the server in write(), so it is a
  restriction and not a rendering trick. A condition that will not
  evaluate is treated as matching, so a typo locks the field rather
  than quietly unlocking it. Odoo 16 serialises modifiers as domains
  and cannot carry an expression, so there the condition is enforced on
  the server only.
- **Manager hierarchy access.** Two new sentinels, `__subordinate_uids__`
  and `__team_uids__`, resolved by walking hr.employee.parent_id to any
  depth with a cycle guard. Without the HR app there is no chain, and a
  filter using them matches nothing rather than everything.
- **Soft restriction.** A domain rule can show the records outside its
  filter and refuse only the edits, instead of hiding them.
- **Hide the search panel**, per model.
- **Hide Add Custom Filter and Add Custom Group**, through a small OWL
  patch, because those entries are built by the client and no
  server-side overlay can reach them.
- **Block installing, upgrading and uninstalling apps**, on all six
  ir.module.module buttons.

### Fixed, each one proven live by an adversarial probe
- **A forged context voided the whole gate.** `env.company` reads
  allowed_company_ids out of the RPC payload and RAISES when it names a
  company the user may not enter; that exception travelled up through
  every profile lookup and switched the overlay off. Company resolution
  now falls back to the user's own company, which cannot be forged.
- **A forged timezone moved a time window by up to a day.** The cache
  date came from `fields.Date.context_today`, which reads tz from the
  context. It now reads the timezone stored on the user.
- **Hidden fields were returned in full by search_read.** Only read()
  was overridden; the funnel is `_read_format`, which search_read and
  web_search_read format through.
- **Hidden reports still rendered from their URL.** The controllers do
  not always go through `_render`; each per-type renderer is gated now.
- **Read-Only Mode silenced the chatter.** Odoo derives message access
  from write access on the document, so a read-only profile stopped a
  user posting anywhere. Posting, logging, subscribing and scheduling
  an activity now run under the internal marker: talking is not
  editing, and the data stays read only.
- **An export path only checked its first segment**, so
  parent_id/hidden_field walked a hidden value straight out.
- **Duplicate could be reached through copy_data()**, and a record
  could be created already archived to sidestep the archive rule.

99 in-database tests plus 18 standalone, green on Odoo 16, 17, 18
and 19.

## v19.0.1.4.0 (September 2026)

Fourth pass, from a second adversarial audit of the enforcement layer.

- **Sign-in gate moved to `_login`.** It lived in `_check_credentials`,
  which is the password path only, so an authentication provider such
  as OAuth signed a disabled user straight in. `_login` is the funnel
  every interactive sign-in goes through.
- **The sign-in gate no longer fails open.** It caught every exception
  except AccessDenied and logged it, so any error inside the gate let
  the sign-in through, on the one path it exists to guard.
- **The operation gate no longer fails open by accident.** The profile
  lookup returned an empty tuple both when a user had no profile and
  when the lookup itself failed, so a failure read as "unrestricted".
  It now returns None for failure, and unknown means blocked.
- **Locked fields are reset to their default on create, not dropped.**
  Dropping the key left a locked NOT NULL column to the database, which
  answers with a constraint violation that aborts the cursor rather
  than an access error. A field that is genuinely required at the ORM
  level is refused explicitly instead, with a message naming the field.
  Every reset is logged, because a silent drop is data loss.
- **A field that is both locked and required no longer bricks the
  model.** The profile contradicts itself; the runtime now ignores the
  required half rather than making the model impossible to create.
- **get_views pruning gained the guards the action filter already had**:
  never remove `search`, never leave the client with no view to render.
- **A profile whose window opens today now invalidates the caches.**
  Expiry archives the profile, which invalidated; an opening window had
  no event at all, and core caches the menu tree with no date in its
  key, so the daily job now covers both ends.

83 in-database tests plus 18 standalone, green on Odoo 16, 17, 18
and 19.

### Known and documented
A read-only profile cannot post to the chatter, because Odoo derives
message access from write access on the document. That is core
behaviour, not a setting in this module.

## v19.0.1.3.0 (September 2026)

Closes the three axes an adversarial audit showed were still
presentation rather than restriction.

- **A hidden field is now hidden from the API.** `read()` blanks
  profile-invisible fields, which covers read, web_read and
  search_read, while `record.field` keeps working for server code so
  computes, constraints and other modules are unaffected. Combined with
  the existing write and export gates, hiding a field now means the
  client and the API cannot see it, write it or export it.
- **A hidden report can no longer be run from its URL.** The gate sits
  on `ir.actions.report._render`, the dispatcher every report type goes
  through, and on `report_action`, so `/report/pdf/...` answers the same
  rule as the cog menu.
- **copy() can no longer be used to write a locked field.** It is
  reachable over RPC and its `default` argument is caller supplied,
  while the internal marker that lets a duplicate carry the source
  record's locked values was switching the field gate off. The caller's
  own overrides are now stripped first.

80 in-database tests plus 18 standalone, green on Odoo 16, 17, 18
and 19.

## v19.0.1.2.0 (September 2026)

Second enforcement pass. The v19.0.1.1.0 gate was real but it was
written without asking what it would break, and an adversarial re-audit
found that it broke a lot. Everything below was verified on a running
Odoo 19 and then across 16, 17 and 18.

### Fixed, in the enforcement layer added by the previous release
- **Hiding one field made the model uncreatable.** The web client posts
  every field in the form on save, the hidden one included, so raising
  on its presence blocked creation entirely. Locked fields are now
  STRIPPED from a create payload instead: a new record has no previous
  value to protect, and the submitted value is discarded rather than
  accepted.
- **The field gate had a caller-controlled switch.** It decided whether
  a write was a user edit by reading `request.params["method"]`, which
  the caller supplies, and which also missed first-party paths such as
  `web_save_multi`. That heuristic is gone. A locked field now raises
  whenever the value would actually change, for every caller.
- **Read-Only Mode broke the session.** It blocked create on transient
  records, so every wizard stopped working, along with chatter
  messages, activities, attachments and saved filters. It is now scoped
  to business models, with that infrastructure exempt by name.
- **A required rule froze existing records.** The check ran over every
  required field after each write, so a record that predated the rule
  could not be saved at all. On a write it now judges only the fields
  that write touched.
- **Unarchiving was not gated**, only archiving. `write({'active': ...})`
  is now checked in both directions.
- **copy() tripped the field gate**, because the duplicate carries the
  source record's locked values. The module's own multi-step operations
  now run under an internal marker, which is a thread-local rather than
  a context key so no caller can set it.
- **One sudo menu load poisoned a user's menu cache.** Core caches the
  menu tree per uid with no superuser dimension, so bailing out on
  `env.su` un-hid every hidden menu for the rest of the cache lifetime.
  The hidden set is also resolved from the user's own companies rather
  than from the company switcher, which has no place in that cache key.
- **The cross-user leak test could not fail**: it cleared the registry
  cache before every read, which is precisely the state the leak needs
  to survive. It now reads the second user without clearing.
- **Creating a user with profiles attached skipped invalidation**; only
  write was covered.
- `registry.signal_changes()` is no longer called inline. It cleared
  `cache_invalidated` mid-transaction, so the framework's own
  post-commit signal had nothing left to send to other workers.

### Added
- Import and export are enforced on the data path: `load()` and
  `export_data()` are gated, and a hidden field cannot be exported.
- Demo data: four worked example profiles, assigned to nobody, so a
  fresh database opens on something to read and restricts no one.
- `tools/backport_access.py` and `tools/test_runner_access.py`.
- 95 automated tests (77 in-database plus 18 standalone), green on
  Odoo 16, 17, 18 and 19.

### Cross-version
The module now runs on 16, 17, 18 and 19 from one source. The gate
implements both framework contracts (`_check_access` on 18 and 19,
`check_access_rights` on 16 and 17), domain composition works with
`odoo.fields.Domain` or `odoo.osv.expression`, the field overlay writes
both the modern attribute dialect and the Odoo 16 `modifiers`
dictionary, and administrator detection reads whichever membership
field the series exposes. The backport tool is left with view tags,
the `groups_id` rename, the Odoo 16 `attrs` syntax and product wording.

### Honest limitations recorded on the listing
- A hidden view type is removed from window actions stored in the
  database; an action another module builds in Python is not rewritten.
- The spreadsheet switch relies on a session flag that only exists from
  Odoo 18, so the 16 and 17 listings do not claim it.
- Locking a field also stops a workflow button writing it, when that
  button runs as the restricted user.

## v19.0.1.1.0 (September 2026)

Enforcement release. A line-by-line audit against a competing access
app found that most of what the product advertised was applied to the
rendered view and nothing else, and that several switches did nothing
at all. This release closes that gap and makes the remaining
interface-level behaviour explicit in the product itself.

### Fixed (each defect is now covered by a regression test)
- **Rule edits had no effect until a server restart.** The overlay is
  stamped during view post-processing, which Odoo caches in the
  `templates` ormcache category, but invalidation only cleared
  `default`. `_invalidate_active_cache` now clears both. This affected
  every UI rule on an existing profile.
- **The record-rule gate could be switched off by the caller.** The
  `eh_access_studio_bypass` context key arrives from the RPC payload
  verbatim; it is now honoured only when `env.su` is set.
- **The gate failed open.** An exception inside the extension returned
  the unrestricted core domain. It now fails closed for users who are
  under an active profile, and stays open for everyone else so a bug
  cannot take a database down.
- **Cross-user menu leak.** Filtering inside `search_fetch` placed our
  result in core's `_visible_menu_ids` cache, keyed on the group set
  and not on the user, so one profiled user stripped menus from
  colleagues with the same groups. Moved to `_load_menus_blacklist`
  and `get_user_roots`, both resolved per user.
- **The chatter patch broke the Activity button on every database.**
  `position="attributes"` on `t-if` overwrote core's
  `props.has_activities`; the two conditions are now combined.
- **`disable_login` never fired on browser sign-in.** Core sudoes the
  user before `_check_credentials`, and the gate bailed out on
  `env.su`. The denial audit line is now written on an independent
  cursor so it survives the failed transaction.
- **`disable_debug_mode` was dead code** (two arguments passed to a
  three-argument signature, swallowed by a broad except). Developer
  mode is now cleared on the session in `ir.http._handle_debug`, which
  covers every route rather than one URL.
- **`hidden_view_types` broke the web client** instead of hiding a
  view: the switcher is built from `action.views`, so the mode is now
  removed from the action itself, and never the last remaining mode.
- **`restrict_archive` guarded a method the client no longer calls.**
  `action_archive` and `action_unarchive` are now gated as well.
- **`hide_spreadsheet` and `hide_add_property` enforced nothing.**
  Spreadsheet insertion is switched off through `session_info`, and
  custom-properties fields are removed from the arch.
- **YAML import crashed on any profile that hid a menu**, because
  `ir.ui.menu.complete_name` has no search method. Three of the five
  shipped example files could not be imported. Menus are now resolved
  in Python, and `hide_external_link`, hidden reports and hidden
  server actions survive the round trip instead of being dropped.
- **Re-importing an archived profile created a duplicate.** Both the
  importer and the uniqueness constraint now search with
  `active_test=False`.
- **The administrator lockout guard missed implied administrators.**
  It now reads `all_user_ids`, not direct membership.
- `force_save` was stamped on read-only fields, which pushed the
  locked field back into every save. Removed.
- `no_edit` is not a recognised many2one option; the injected options
  are now `no_open`, `no_create`, `no_create_edit`, `no_quick_create`.
- The chatter strip matched only the Odoo 18+ `<chatter/>` tag, making
  it a silent no-op on older series. Both shapes are handled.

### Added
- **Server-side enforcement.** Read-only mode and the per-model Hide
  Create / Hide Edit / Hide Delete switches are enforced through
  `BaseModel._check_access`, and hidden or read-only fields can no
  longer be written, over any path. Profile-required fields are
  validated on create and write. Reading is deliberately untouched:
  restricting what a user may see is the job of Domain Access.
- `tests/test_enforcement.py`, one regression test per defect above.
- Honest in-product notes: the Model Access and Hide Fields tabs now
  state which switches block at the ORM and which are interface level.

### Documentation
- Removed the claim of a per-operation audit log from
  `models/eh_access_domain.py`; only denied sign-ins and profile
  expiry are recorded, and the reasoning is written down.
- Corrected the pricing line in `docs/POSITIONING.md`: the module is
  free under LGPL-3.

## v19.0.1.0.0 (May 2026, initial release)

### Capabilities
- Hide menus and submenus from navigation.
- Hide fields per-model: invisible / read-only / required.
- Hide individual buttons, notebook tabs, kanban links, search filters
  and group-by entries by technical name.
- Hide chatter system-wide, per-model, or per-individual-button (Send
  Message / Log Note / Schedule Activity).
- Per-model toggles for create / edit / delete / archive / duplicate /
  import / export / spreadsheet / add property; hidden view types;
  hidden report and server actions.
- Per-model record rules (Domain Access) with smart placeholder
  resolution. Sentinels: `__uid__`, `__cid__`, `__company_ids__`,
  `__today__`, `__yesterday__`, `__tomorrow__`, `__week_start__`,
  `__week_end__`, `__month_start__`, `__month_end__`,
  `__quarter_start__`, `__quarter_end__`, `__year_start__`,
  `__year_end__`, `__last_7_days__`, `__last_30_days__`,
  `__last_90_days__`, `__last_365_days__`.
- Time-bound profiles with `date_from` / `date_until`. Daily cron
  deactivates expired profiles in bulk.
- Disable login per profile, enforced at the credential check. Audit
  message posted on the profile chatter on denial.
- Disable developer mode per profile (see v19.0.1.1.0: this was not
  functional until the session-level gate landed).
- Profile-level `readonly` mode (admins exempt by constraint).

### Operations
- Four-template quick-start wizard: read-only, own-records, vendor /
  portal, auditor.
- Health-check action: flags orphan, no-op, expired and unparseable
  profiles.
- Conflict-report action: flags users in two profiles with
  contradictory domain rules on the same model.
- Diagnostic / Summary action: plain-English explanation of every
  rule on a profile.
- Test-plan action: lists target users for ad-hoc impersonation in a
  private browser window.
- Profile duplication action.
- YAML import / export wizard with idempotent re-import (upserts by
  name). 1 MB payload cap.

### Performance and safety
- Five `@tools.ormcache`-decorated methods keyed on
  `(uid, cid, today_iso, model_name)`: `_get_active_profile_ids`,
  `_model_line_ids_for`, `_chatter_visibility_for`,
  `_eh_access_field_rules`, `_eh_access_node_rules`. All sit in the
  default cache category (Odoo 19's ormcache categories are not
  extensible; per-method clear is not exposed by the framework).
- Invalidation via `registry.clear_cache()` (default category only)
  plus a single `registry.signal_changes()` for cross-worker
  propagation. Categories outside the default (assets, templates,
  routing, groups) stay warm.
- Cron deactivation skips per-row invalidation via an
  `eh_access_studio_skip_invalidate` context flag, then fires one
  invalidation + signal at the end of the batch regardless of size.
- Parameterised SQL only. No `.format()` or `% (...)` with values.
- `psycopg2.errors.UndefinedTable` caught in cache lookups so the
  first-install path does not crash before model tables exist.
- Fail-closed on unparseable Domain Access filters: returns
  `[("id", "=", False)]` instead of granting access.
- Admin-in-readonly profile rejected at constraint level. Access
  Studio configuration menu is exempt from any rule that targets it.
- Domain Access bypass for our own configuration tables to keep
  admins out of self-imposed lockouts.

### Schema
- Models: `eh.access.profile` (mail.thread + activity), `eh.access.field`,
  `eh.access.model`, `eh.access.node`, `eh.access.chatter`,
  `eh.access.domain`, plus the abstract `eh.access.line.mixin` for
  CRUD-driven cache invalidation.
- Wizards: `eh.access.yaml.wizard`, `eh.access.template.wizard`.
- Privilege groups: `Access Studio: User` (read-only),
  `Access Studio: Manager` (full).

### Listing and docs
- Heritage dark-and-mint listing page with DM Sans / DM Mono
  typography.
- HOWTO guide with cookbook, three-layer enforcement explainer,
  troubleshooting and engineering-rule cross-reference.
- Four sample YAML files in `docs/examples/` matching the four
  template scenarios.
- Positioning one-pager in `docs/POSITIONING.md`.
- i18n template seeded in `i18n/eh_access_studio.pot`.

### Tests
- 18 standalone resolver tests (no Odoo loaded), runnable via
  `python3 tools/test_domain_resolver.py`.
- 12 Odoo test suites covering: profile lifecycle, hide menu, hide
  field, domain rules, disable login, fail-closed, caching, YAML
  round-trip, conflict and diagnostic, template wizard, health
  check.

### Compliance
- License LGPL-3.0 or later.
- Apps Store: name 22 chars, banner 880x440, icon 140x140, no
  external links, English only, contact `info@erpheritage.com.au`.
- Originality position recorded in
  `../docs/ORIGINALITY_AND_LICENSING.md`.
