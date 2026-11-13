"""The texts of the providers that ship with the planner, so that they can be translated.

A provider describes its settings in English in the JSON Schema it publishes. The interface shows
a text in the language of the page when it knows a translation, and as the provider wrote it
otherwise, so a provider installed later needs nothing here. A test checks that every text of the
built-in providers is listed and translated.
"""

from __future__ import annotations

from planner_ui.i18n import N_

BUILT_IN_TEXTS: tuple[str, ...] = (
    N_("CSV file (absences)"),
    N_("File"),
    N_("Path of the CSV file below the import directory, for example absences.csv."),
    N_("CSV file (people)"),
    N_("Path of the CSV file below the import directory, for example people.csv."),
    N_("Keycloak"),
    N_("Keycloak URL"),
    N_(
        "Address of Keycloak as the API reaches it, for example http://keycloak:8080 "
        "(without /realms/...)."
    ),
    N_("Realm"),
    N_("Client ID"),
    N_("Client with a service account that may view users and query groups."),
    N_("Client secret"),
    N_("Verify the certificate"),
    N_("Switch off only for tests."),
    N_("Realm roles to read"),
    N_(
        "One per line. Only the holders of these roles are told apart by role, for "
        "the mapping's 'include roles'. Needs the right view-realm."
    ),
    N_("Page size"),
    N_("Jira"),
    N_("Jira edition"),
    N_("CLOUD: Jira Cloud (atlassian.net). DATA_CENTER: Jira Data Center or Server."),
    N_("Jira URL"),
    N_(
        "Address of Jira, for example https://example.atlassian.net. Links to the issues point "
        "there."
    ),
    N_("API URL"),
    N_(
        "Only for a scoped API token of Jira Cloud: https://api.atlassian.com/ex/jira/<cloud id>. "
        "Empty: the Jira URL."
    ),
    N_("E-mail address"),
    N_("Jira Cloud: the account the API token belongs to. Empty for Data Center."),
    N_("API token"),
    N_("Jira Cloud: an API token of the account. Data Center: a personal access token."),
    N_("Projects"),
    N_("SELECTED: only the projects chosen below. ALL: every project the account can see."),
    N_("Selected projects"),
    N_("Load the projects from Jira and tick the ones to import."),
    N_("Issue types"),
    N_(
        "Only issues of the ticked types. None ticked: every standard type, without epics and "
        "sub-tasks, so that no work is counted twice."
    ),
    N_("Leave out issues done longer ago (days)"),
    N_("A complete read skips issues finished more than this many days ago. 0: read them all."),
    N_("Additional JQL"),
    N_("Added to the query with AND, for example labels = planning."),
    N_("Story points field"),
    N_("Id or name of the field, for example customfield_10016. Empty: found by its name."),
    N_("Start date field"),
    N_("Id or name of the field. Empty: found by its name or type."),
    N_("Effort fields"),
    N_(
        "Only for the effort strategy CUSTOM_FIELDS: ids or names of the fields that hold an "
        "effort, for example Effort Backend. The mapping assigns them to skills."
    ),
    N_("CSV file (tasks)"),
    N_("Path of the CSV file below the import directory, for example tasks.csv."),
    N_("Effort strategy"),
    N_(
        "EVEN_SPLIT shares the estimate over the labels that name a skill; "
        "LABEL_ENCODED reads the effort from labels such as be-3; CUSTOM_FIELDS "
        "reads one field per skill; MANUAL imports no effort."
    ),
    N_("Unit factor"),
    N_("Person-days per unit of the estimate (story points x factor, hours / 8 is 0.125)."),
    N_("Label pattern"),
    N_("LABEL_ENCODED: a regular expression with the groups 'skill' and 'effort'."),
    N_("Label skills"),
    N_("Label to skill key, when they differ. Matching ignores case."),
    N_("Unknown labels"),
    N_("What to do with a label that names no skill."),
    N_("Custom field skills"),
    N_("CUSTOM_FIELDS: skill key to the name of its field."),
    N_("Status map"),
    N_("State of the source to open, done or cancelled. The usual names are known already."),
    N_("Team map"),
    N_("Team hint of the source (project, component) to team."),
    N_("Missing"),
    N_(
        "CLOSE cancels open tasks that the source no longer returns; KEEP leaves "
        "them. Only a complete read of the source can tell."
    ),
    N_("Include groups"),
    N_("Import only people in one of these groups. Empty: everybody."),
    N_("Include roles"),
    N_("Import also people with one of these roles. Empty: no filter by role."),
    N_("Group teams"),
    N_(
        "Group=Team, one per line, the most important first. A person gets the team "
        "of the first of their groups that is listed. Without '=' the team has the "
        "group's name."
    ),
    N_("Create teams"),
    N_("Create a team that a listed group names but that is missing."),
    N_("Default team"),
    N_(
        "Team of the people without a listed group. It must exist already. Without "
        "it these people are reported and not imported."
    ),
    N_("Group skills"),
    N_(
        "Group=Skill, one per line. People in the group get the skill added; the "
        "import never takes a skill away."
    ),
    N_("Default allocation percent"),
    N_("Share for new members. Default: the setting."),
    N_("CLOSE deactivates members that the directory no longer returns; KEEP leaves them."),
    N_("Type map"),
    N_("Type code of the source to the key of an absence type."),
    N_("Lookback days"),
    N_("Days before today to read."),
    N_("Lookahead days"),
    N_("Days after today."),
    N_(
        "CLOSE removes absences of this integration that the source no longer "
        "reports within the days read; KEEP leaves them."
    ),
)
