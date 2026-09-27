# Users & Groups

Two things on one screen: the **people** and the **rights** they carry. Users hold
roles, groups bundle additional rights and decide about the slice of data visible
to them — together they answer what somebody may do and see.

![A group's permission matrix](/help-assets/group-permissions.png)

## Roles

Every user has one or more roles. They are the baseline; a group adds to them or
takes from them.

| Role | Meaning |
| --- | --- |
| **Viewer** | May read: objects, definitions, rules, runs. |
| **Editor** | May also maintain: create/change/delete/publish objects, relations, start jobs. |
| **Administrator** | May also manage: users, groups, definitions, units, channels, integrations, system logs. |
| **Superadmin** | May do everything, system-wide — and is restricted by **nothing**. |

Only the **superadmin** has that special position: it is exempt from every check,
including a data scope. An administrator **is** restricted by a scope. The last
active superadmin can neither be deleted nor demoted.

## Creating and maintaining users

The dialog asks for username, email, password, roles, status and language.
Username and email are required, everything else has a sensible default.

- **Password** — at least 8 characters and different from the previous one. When
  creating a user or setting a password as an administrator, the switch
  **Require a new password** decides whether the user has to set their own
  password on first login. Off for passwords you hand over as final — for an
  integration account, say.
- **Status** — `active` or `suspended`. A suspended user cannot log in, and a
  running session ends immediately.
- **Language** — the interface language for this user.
- **Password column in the list** — `new` means the user never set a password of
  their own, `ok` means done.
- **Login as** (superadmin only) — takes over another user's session for testing
  and support. It is **not** a transfer of rights: the session runs with that
  user's roles and is logged; a banner leads back.

## Groups: bundling rights

A group has permissions, a data scope, members and a position in the hierarchy.
The **effective rights** of a person are:

> the role default **+** everything their groups allow (parent groups included)
> **−** everything one of their groups explicitly denies.

A **deny always wins**. A group without permission entries in either list changes
nothing — it is the convenient way to bundle only a data scope or only a
membership.

## The permission matrix

Every row is one permission, and every row carries three answers: **as the role**
(the default, enter nothing), **allow** (grant additionally) or **deny** (take away
explicitly). The answers are three small icon buttons — the tooltip names them:

- **grey** (a circle with an arrow) — as the role says,
- **green** (a check) — additionally allowed,
- **red** (a block sign) — explicitly denied.

The **filled** button is this group's answer, so a glance down the column shows what
the group changes. Next to it stands the role whose default applies — so "as the
role" needs no lookup. The matrix offers exactly the permissions the system knows;
an unknown key is refused on save, so a typo cannot look like a set right.

## The data scope

The scope answers: **which data** does this group see? It is built from blocks —
object type, status, category, collection — and is empty by default: without a
block the group sees the whole catalogue. Several blocks all apply together
("products that are published **and** in this category").

Underneath the blocks the scope is spelled out **in words**, with a **counter**
that measures at the core what the scope shows ("visible: 2,000 of 11,633
objects"). The counter is checked before saving, but it is not a promise: it shows
what is in force right now.

Two things worth knowing:

- **Category and collection blocks mean the direct assignment.** An object that is
  only assigned to a subcategory does not fall under it. To include a whole
  branch, pick several categories.
- **A block without values does not count** — it is a scope only once it has
  values; the counter says so instead of inventing a number.

Rules that do not come from the assistant (an import, an older document) are kept:
the editor shows them verbatim and stores them unchanged.

## Asking for a suggestion (optional)

If the AI assistance is switched on and configured in the system settings, a
**Create suggestion** button appears next to **Permissions**. It opens a dialog:
describe in your own words what the group may do and which data it should see
("may not delete anything and only sees published objects"), choose **Write new**
or **Improve an existing one**, and generate the suggestion.

The suggestion fills the permission matrix and the **object type** and **status**
blocks — it is the complete state it proposes, so everything it does not name
falls back to "as the role". **Category and collection blocks stay unchanged**,
because they name individual objects the suggestion cannot know; if a wish cannot
be expressed that way, the dialog says so as a note. **Nothing is saved
automatically:** review the matrix, the scope and the counter, then press **Save**.

## Members

Members are written immediately, not saved — the **Members** section of the group
dialog adds a user through a picker and removes them again. Next to the direct
members stand the **inherited** ones: whoever is in a child group also carries the
parent's rights. Those rows are labelled "via <child group>" and cannot be removed
here — they belong to the other group.

Two notes appear exactly when they apply:

- A **superadmin** in the group: their membership is inert, since they may do
  everything anyway.
- An **administrator** in a group **with** a data scope: that scope restricts them —
  only a superadmin is exempt.

## Hierarchy

The hierarchy passes rights **upwards**: a child group hands its rights to its
parent groups — never the other way round. Every group may have several parents
and several children. A cycle is impossible; the core refuses such an attempt with
a message the dialog shows in full.

## Effective rights of a user

The question "why may Peter do that?" is answered by the **user** dialog: it loads
the rights of the opened user from the core and names the source of every one.

![The effective rights of a user](/help-assets/user-rights.png)

- The **summary line** adds it up: roles **+** groups **⇒** how many of the rights
  are in force.
- Below it stand only the **deviations** from the role default: additionally
  granted and explicitly denied rights, each with the group that says so. A green
  entry comes from a group, a red one was denied.
- Behind **Every permission of the catalog** is the full list with the state of
  each permission: `role` (default), `group` (granted additionally), `denied`, and
  `superadmin` for a superadmin.
- A **↳** next to a group means: that group counts through the hierarchy, the
  person is not a direct member.

## What every change does

- **Rights take effect immediately.** Every change to permissions, groups,
  memberships or passwords ends the running sessions of the **other** users — they
  sign in again and work with the new state. Whoever makes the change stays signed
  in.
- **Everything is logged.** Creating, changing, deleting, memberships, hierarchy
  and password changes all appear in the audit log.
- **Groups are not objects.** Users and groups live in their own tables and
  therefore never show up in the product list, in an import or in an export.
