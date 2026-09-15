# Getting started

Meadow PIM manages your product information: objects, their attributes, their
relationships and their releases to output channels. This chapter explains the
interface and the vocabulary used by every other chapter.

## Signing in

1. Open the address of your Meadow instance in a browser.
2. Enter your username and password.
3. You land on the **Dashboard**.

If you were signed out because your session expired, the login page says so, and
after signing in you return to the page you were heading for.

Your access is created by your administration. A fresh instance ships with a
pre-configured administrator account — change its password immediately.

## This handbook

This page is the handbook. The chapters are on the left, the search box on top,
and the handbook language is chosen at the top right (independently of the
interface language).

![The handbook page with its chapter list and search](/help-assets/help.png)

Wherever Meadow shows an **info button** (❓), its tooltip answers in one sentence
and a click takes you to the matching chapter.

## Changing your password

The **key icon** in the header opens the password dialog. It asks for the current
password and a new one with a minimum length. As long as the password has never
been changed, a banner in the interface reminds you.

## The interface

- **Header** — signed-in user, password change, sign out.
- **Navigation** (left) — every working area.
- **Language selector** (bottom of the navigation) — the language of the
  **interface**. It is unrelated to the languages of your product data.
- **Working area** (right) — the page you selected.

Your role decides which menu entries and buttons are available: editing requires
an editor role, the administration pages (attribute definitions, categories,
jobs, users, rules, connectors …) require an administrator role.

## Dashboard

The dashboard is your home page:

- **Objects** — the number of objects per type.
- **Completeness** — how many objects of each type still miss required values.
  Clicking a tile opens the filtered object list.
- **Status** — the distribution across workflow statuses.
- **Recent activity** and **recent jobs** — the latest logged events and
  background runs.

## Where to find what

| Task | Chapter |
| --- | --- |
| Find, filter and edit objects in the table | [Object list (grid)](/help?page=product-grid) |
| Maintain a single object | [Editing an object](/help?page=product-form) |
| Bring in data from CSV, Excel or JSON | [Importing data](/help?page=import) |
| Hand out data as CSV, Excel, JSON or BMEcat | [Exporting data](/help?page=export) |

## Good to know

- **Everything is an object.** Products, categories, assemblies and
  classification nodes share one data model and differ only by their **type**.
- **Attributes are defined.** Which fields an object type may have is decided by
  the attribute definitions — not every object shows the same fields.
- **Values can be inherited.** In the structure (assembly → part) you maintain
  shared values only once.
