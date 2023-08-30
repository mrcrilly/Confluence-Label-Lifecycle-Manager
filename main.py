import argparse
import concurrent.futures
import time
import matplotlib.pyplot as plt
import numpy as np

from string import Template
from datetime import datetime, timedelta
from dateutil.parser._parser import ParserError
from multiprocessing import current_process
from dateutil.parser import parse
from dateutil.relativedelta import *
from atlassian import Confluence

from requests.exceptions import HTTPError
from atlassian.errors import ApiError

# DEBUG will go bonkers and print *a lot* of debugging information that can be
# useful for, well, debugging, obviously.
DEBUG = False
INFO = False

# The Atlassian Confluence client that's used for all API calls
client = None

# Labels to be removed from the pages we discover as they've been deprecated.
deprecated_labels = [
  "fresh",
  "stale",
  "rotten"
]

# The labels we want to apply to pages.
# 
# !!!
# WARNING: DO NOT EDIT THIS DIRECTLY. THE ORDER IS IMPORTANT.
# !!!
target_labels = []

# Using "lifecycle_ignore" comes in two flavours:
#  1. Simply use "lifecycle_ignore" to ignore the page forever
#  2. Use "lifecycle_ignore=<ISO>" to ignore the page until *after* <ISO>
#  2.1 <ISO> is an ISO formatted date without time (zone) information: 20230101, 20210918, <YYYY><MM><DD>
lifecycle_ignore_tag = "lifecycle_ignore"

def discover_all_pages_in_space(space, max=100, limit=500):
  """Finds all pages inside of space, to a max number of pages, limit pages at a time.
  
  Keyword arguments:
  space -- the name of the space in Atlassian Confluence, i.e. "AA"
  max -- the maximum numbers of pages to manage (default: 100)
  limit -- the maximum number of pages to request in each API call (default: 500)
  """

  if DEBUG: print(f"discover_all_pages_in_space({space}, {max}, {limit})")

  start = 0
  pages = []

  if limit > max:
    if DEBUG: print(f"discover_all_pages_in_space({space}, {max}, {limit}): {limit} found to bigger than {max}, so setting limit to max")
    limit = max

  count = 0
  while count < max:
    all_pages = client.get_all_pages_from_space(space, start=start, limit=limit)
    count = len(all_pages)
    if DEBUG: print(f"discover_all_pages_in_space({space}, {max}, {limit}): total pages found this cycle: {count}")

    start += limit

    if count == 0:
      break

    if count > 0:
      pages = pages + all_pages

  return pages

def discover_page_labels(page_id):
  """Discovers all the labels that a page already has.
  
  Keyword arguments:
  page_id -- the Confluence page ID to work with.
  """

  if DEBUG: print(f"discover_page_labels({page_id})")

  page_labels = client.get_page_labels(page_id)
  
  if len(page_labels['results']) == 0:
    if DEBUG: print(f"discover_page_labels({page_id}) has no labels")

    return {
    "page_id": page_id,
    "labels": None,
  }

  if DEBUG: print(f"discover_page_labels({page_id}) has labels: {page_labels['results']}")

  return {
    "page_id": page_id,
    "labels": page_labels['results'],
  }

def discover_page_state(page_id, rotten_days=180, stale_days=90):
  """Determines the state of the page in terms of its lifecycle phase.
  
  Keyword arguments:
  page_id -- the Confluence page ID to work with.
  rotten_days -- the number of days since the last update after which a page is considered rotten (default: 180)
  stale_days -- the number of days since the last update after which a page is considered stale (default: 90)

  Returns dict:

    {
      "page_id": string(page's id),
      "created_by": {
        "id": string(page's id),
        "name": string(author name),
        "email": string(author's email),
      },
      "last_edited": {
        "by": {
          "id": string(page's id),
          "name": string(author's name),
          "email": string(author's email)
        },
        "when_raw": string(datetime),
        "when": datetime,
      },
      "state": string(page's lifecycle phase),
    }

  """

  if DEBUG: print(f"discover_page_state({page_id})")

  page_properties = client.history(page_id)

  # TODO: I hate how I'm having to chop the string up. I couldn't get string parsing working
  # with the "T" and "Z" in there, so I gave up and bruteforced it, as you can see below. Ideally
  # Python's datetime string parsing would be used instead.
  last_updated_clean = page_properties['lastUpdated']['when'].replace("T", " ")
  last_updated_cleaner = last_updated_clean.replace("Z", "")

  if DEBUG: print(f"discover_page_state({page_id}) was last updated {last_updated_cleaner}")

  last_update = datetime.strptime(last_updated_cleaner, '%Y-%m-%d %H:%M:%S.%f')
  date_rotten = datetime.now() - timedelta(days=rotten_days)
  date_stale = datetime.now() - timedelta(days=stale_days)

  # We strip off the "(Deactivated)" part of the name as we don't care about it.
  created_by = {
    "id": page_properties['createdBy']['accountId'],
    "name": page_properties['createdBy']['publicName'].replace(' (Deactivated)', ''),
    "email": page_properties['createdBy']['email'],
  }

  # The "by" object is included as meta data, but is currently unused.
  # 
  # TODO: email author (created_by["email"]) or whoever last edited the page (last_edited["by"]["email"])
  # informing them of the lifecycle state change?
  last_edited = {
    "by": {
      "id": page_properties['lastUpdated']['by']['accountId'],
      "name": page_properties['lastUpdated']['by']['publicName'].replace(' (Deactivated)', ''),
      "email": page_properties['lastUpdated']['by']['email']
    },
    "when_raw": page_properties['lastUpdated']['when'],
    "when": last_update,
  }

  # Uses Python's built-in ability to compare dates to determine if the page's created/last edited
  # datetime falls below the dates we're interested in.
  state = target_labels[0]
  if last_update < date_rotten: state = target_labels[2]
  elif last_update < date_stale: state = target_labels[1]

  if DEBUG: print(f"discover_page_state({page_id}) is considered {state}")

  return {
    "page_id": page_id,
    "created_by": created_by,
    "last_edited": last_edited,
    "state": state,
  }

def action_set_page_label(page_id, desired_label):
  """Sets the page label to the desired label, removing deprecated labels.

  Keyword arguments:
  page_id -- the Confluence page ID
  desired_label -- the label we want to apply to the page
  """

  if DEBUG: print(f"action_set_page_label({page_id}, {desired_label}):")
  existing_labels = client.get_page_labels(page_id)

  # This little loop-in-a-loop will remove any deprecated labels.
  for label in existing_labels['results']:
    current_label = label['label']
    for deprecated in deprecated_labels:
      if deprecated == current_label:
        if DEBUG: print(f"action_set_page_label({page_id}, {desired_label}): found deprecated label {deprecated}")
        client.remove_page_label(page_id, current_label)

  # Undesirable labels are different to deprecated labels. Undesirable labels are the two labels
  # we don't want out of the three we apply to documents. So if we want to apply "fresh" then the
  # labels "stale" and "rotten" are undesirable and should be removed if they're present
  undesirable_labels = [x for x in target_labels if x != desired_label]
  if DEBUG: print(f"action_set_page_label({page_id}, {desired_label}): has the following undesirable labels: {undesirable_labels}")

  labelling_required = True
  triggered_lifecycle_exception = False
  lifecycle_exception_until = ""
  for existing_label in existing_labels['results']:
    current_label = existing_label['label']
    
    # First we check to match sure we're not meant to completely ignore the lifecycle of this particular page
    if current_label.startswith(lifecycle_ignore_tag):
      if not current_label.count('=') > 0:
        # Completely ignore the page
        labelling_required = False
        triggered_lifecycle_exception = True
        break
      
      # Work out whether or not we're inside of the ignore window
      split = current_label.split("=")
      if split[1] == "":
        # We'll assume they meant "lifecycle_ignore"
        labelling_required = False
        triggered_lifecycle_exception = True
        break

      # Now try to parse their date tag and work with it
      try:
        parsed_date = parse(split[1])
        if not datetime.now() > parse(split[1]):
          # We're still inside the lifecycle exclusion window, so ignore the page
          labelling_required = False
          triggered_lifecycle_exception = True
          lifecycle_exception_until = split[1]
          break

      except ParserError:
        # We have a bad lifecycle label that we're going to ignore
        # and label the page as normal
        # TODO: probably need a better solution, like a comment on the page?
        pass

    if labelling_required and (current_label in undesirable_labels):
      # We've found an undesirable label, so we need to remove it as it's
      # not the same as the one we're going to add
      try:
        client.remove_page_label(page_id, current_label)
      except (ApiError, HTTPError) as e:
        if DEBUG: print(f"action_set_page_label({page_id}, {desired_label}): resulted in API error: {str(e)}")
        continue

    if labelling_required and (current_label == desired_label):
      # Ignore the page if it already has the label we're looking to apply
      labelling_required = False
      break

  if labelling_required:
    if DEBUG: print(f"action_set_page_label({page_id}, {desired_label}): requires labelling")

    try:
      client.set_page_label(page_id, desired_label)
    except (ApiError, HTTPError) as e:
      if DEBUG: print(f"action_set_page_label({page_id}, {desired_label}): resulted in API error: {str(e)}")
      return False, False

    return True, False
  else:
    if triggered_lifecycle_exception:
      if DEBUG: print(f"action_set_page_label({page_id}, {desired_label}): DOES NOT require labelling because of a lifecycle label")
      # We're NOT applying a label because a lifecycle label blocked the decision
      return False, True
    else:
      if DEBUG: print(f"action_set_page_label({page_id}, {desired_label}): DOES NOT require labelling because it's currently accurate")
      # We're NOT applying a label because it's simply not required
      return False, False

def configure_atlassian_client(arguments):
  if INFO: print(f"Connecting to {arguments.hostname} and authenticating as {arguments.username} ^_^ !!")

  global client 
  client = Confluence(
    url = arguments.hostname,
    username = arguments.username,
    password = arguments.password,
    cloud = arguments.cloud,
  )

def manage_pages_in_space(arguments):
  all_pages_in_space = discover_all_pages_in_space(arguments.space, max=arguments.maxpages)
  all_pages_and_labels = [discover_page_labels(i['id']) for i in all_pages_in_space]
  
  # Second handle pages with labels
  with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
    thefuture = [executor.submit(discover_page_state, i['page_id'], arguments.rotten, arguments.stale) for i in all_pages_and_labels]
  
  pages_with_states = [f.result() for f in thefuture]
  if DEBUG: print(f"len(pages_with_states)={len(pages_with_states)}")

  all_rotten_pages = [i for i in pages_with_states if i['state'] == target_labels[2]]
  all_stale_pages = [i for i in pages_with_states if i['state'] == target_labels[1]]
  all_fresh_pages = [i for i in pages_with_states if i['state'] == target_labels[0]]
  
  if DEBUG: print(f"len(all_rotten_pages)={len(all_rotten_pages)}")
  if DEBUG: print(f"len(all_stale_pages)={len(all_stale_pages)}")
  if DEBUG: print(f"len(all_fresh_pages)={len(all_fresh_pages)}")

  # Only apply labels if we're not in read-only mode
  if not arguments.readonly:
    # Keep count of the pages that were updated
    rotten_pages_updated = 0
    rotten_pages_lifecycle_triggers = 0
    stale_pages_updated = 0
    stale_pages_lifecycle_triggers = 0
    fresh_pages_updated = 0
    fresh_pages_lifecycle_triggers = 0

    # Process unlabelled pages
    for page in all_rotten_pages:
      change, lifecycle_trigger = action_set_page_label(page['page_id'], target_labels[2])
      if change:
        rotten_pages_updated += 1

      if lifecycle_trigger:
        rotten_pages_lifecycle_triggers += 1
    
    for page in all_stale_pages:
      change, lifecycle_trigger = action_set_page_label(page['page_id'], target_labels[1])
      if change:
        stale_pages_updated += 1

      if lifecycle_trigger:
        stale_pages_lifecycle_triggers += 1
    
    for page in all_fresh_pages:
      change, lifecycle_trigger = action_set_page_label(page['page_id'], target_labels[0])
      if change:
        fresh_pages_updated += 1

      if lifecycle_trigger:
        fresh_pages_lifecycle_triggers += 1
  
  fresh_pages = len(all_fresh_pages)
  stale_pages = len(all_stale_pages)
  rotten_pages = len(all_rotten_pages)
  total_pages_managed = fresh_pages+stale_pages+rotten_pages

  if arguments.readonly and (INFO or DEBUG):
    print("Read-only mode enabled, so no labels were applied")
    print("The following page stats were found:")
    print(f"  Fresh: {fresh_pages}")
    print(f"  Stale: {stale_pages}")
    print(f"  Rotten: {rotten_pages}")
    print(f"  Total: {total_pages_managed}")

  # Only update the central reporting page if we're not in read-only mode
  # and we're being asked to update the page
  if (not arguments.readonly) and arguments.updatepage:
    y = []
    pie_labels = []
    pie_explode = []
    pie_colours = []

    if fresh_pages > 0:
      y.append(fresh_pages)
      pie_labels.append("Fresh")
      pie_explode.append(0)
      pie_colours.append("gray")

    if stale_pages > 0:
      y.append(stale_pages)
      pie_labels.append("Stale")
      pie_explode.append(0)
      pie_colours.append("blue")

    if rotten_pages > 0:
      y.append(rotten_pages)
      pie_labels.append("Rotten")
      pie_explode.append(0.2)
      pie_colours.append("red")

    y = np.array(y)

    plt.pie(y, labels = pie_labels, explode = pie_explode, colors = pie_colours, radius = 1.5)
    plt.savefig('pie.png')
    
    client.attach_file('pie.png', page_id=arguments.pageid)

    page_body = """
    <h2>Warning!</h2>
    <p>This page is <strong>automated!</strong> Do not edit it directly or manually. Your work will be lost when the automated process next runs.</p>
    
    <h2>The Latest Run</h2>
    <ol>
      <li>The last run was on $runDate</li>
      <li>Total number of pages managed: $totalPagesManaged</li>
    </ol>

    <h2>The Pie</h2>
    <p>A visualisation of how the last run applied labels to each page is managed. The pie is not edible.</p>
    <ac:image ac:align="center" ac:height="300">
      <ri:attachment ri:filename="pie.png" />
    </ac:image>

    <h2>Latest Figures</h2>
    <p>here are the latest figures from the latest run:</p>
    <table>
      <tbody>
        <tr>
          <th>Fresh</th>
          <th>Stale</th>
          <th>Rotten</th>
        </tr>
        <tr>
          <td>$freshPages</td>
          <td>$stalePages</td>
          <td>$rottenPages</td>
        </tr>
      </tbody>
    </table>

    <h2>Change Statistics</h2>
    <p>Below we list statistics about how many changes were made in each category:</p>
    <table>
      <tbody>
        <tr>
          <th>Fresh</th>
          <th>Stale</th>
          <th>Rotten</th>
        </tr>
        <tr>
          <td>$freshPagesChanged</td>
          <td>$stalePagesChanged</td>
          <td>$rottenPagesChanged</td>
        </tr>
      </tbody>
    </table>
    
    <h2>Lifecycle Statistics</h2>
    <p>These counters are the number of pages with lifecycle_ignore labels that resulted in no change, even if change was desired by the algorithm.</p>
    <p>For example, if the counter for "rotten" says 100, then on the last run 100 pages were detected as being rotten but were not changed as they had a lifecycle_ignore policy in place.</p>
    <table>
      <tbody>
        <tr>
          <th>Fresh</th>
          <th>Stale</th>
          <th>Rotten</th>
        </tr>
        <tr>
          <td>$freshPagesLifecycleTrigger</td>
          <td>$stalePagesLifecycleTrigger</td>
          <td>$rottenPagesLifecycleTrigger</td>
        </tr>
      </tbody>
    </table>
    """

    page_body_template = Template(page_body)
    page_body_result = page_body_template.substitute(
      runDate=time.ctime(),
      freshPages=fresh_pages,
      stalePages=stale_pages,
      rottenPages=rotten_pages,
      freshPagesChanged=fresh_pages_updated,
      stalePagesChanged=stale_pages_updated,
      rottenPagesChanged=rotten_pages_updated,
      freshPagesLifecycleTrigger=fresh_pages_lifecycle_triggers,
      stalePagesLifecycleTrigger=stale_pages_lifecycle_triggers,
      rottenPagesLifecycleTrigger=rotten_pages_lifecycle_triggers,
      totalPagesManaged=total_pages_managed,
    )
  
    print(f"Updating the central reporting page at ID {arguments.pageid} ^_^ !!")
    
    client.update_page(arguments.pageid, 'Confluence Page Lifecycle Report', page_body_result)
    client.set_page_label(arguments.pageid, 'lifecycle_ignore')

def main():
  parser = argparse.ArgumentParser(description="Confluence Page Lifecycle Processor")

  parser.add_argument("-u", "--username", type=str, help="The Atlassian user to authenticate as.", required=True)
  parser.add_argument("-p", "--password", type=str, help="The Atlassian password to authenticate with.", required=True)

  # These arguments have sensible(?) default values and can be left as is for the most part
  parser.add_argument("-H", "--hostname", type=str, help="The Atlassian URL/hostname to authenticate to.", required=True)
  parser.add_argument("-s", "--space", type=str, help="The Space inside of the Confluence account.", required=True)
  parser.add_argument("-m", "--maxpages", type=int, help="The number of pages to process, maximum.", default=2500)
  parser.add_argument("-U", "--updatepage", help="Update the Lifecycle Report page in Confluence.", action="store_true", default=False)
  parser.add_argument("-I", "--pageid", type=str, help="The Lifecycle Report page ID.")
  parser.add_argument("-T", "--pagetitle", type=str, help="The Lifecycle Report page title.", default="Confluence Page Lifecycle Report")
  parser.add_argument("-c", "--cloud", help="Whether or not the Atlassian instance is Cloud based.", action="store_true", default=True)
  parser.add_argument("-d", "--debug", help="Enable debugging output", action="store_true", default=False)
  parser.add_argument("-i", "--info", help="Enable informational output", action="store_true", default=False)
  parser.add_argument("-S", "--stale", type=int, help="Number of days passed until a page is considered stale", default=90)
  parser.add_argument("-R", "--rotten", type=int, help="Number of days passed until a page is considered rotten", default=180)
  parser.add_argument("-LF", "--freshlabel", type=str, help="The human-readable label for a fresh page", default="lifecycle_phase=fresh")
  parser.add_argument("-LS", "--stalelabel", type=str, help="The human-readable label for a stale page", default="lifecycle_phase=stale")
  parser.add_argument("-LR", "--rottenlabel", type=str, help="The human-readable label for a rotten page", default="lifecycle_phase=rotten")
  parser.add_argument("-r", "--readonly", help="Don't actually apply labels, just output DEBUG/INFO", action="store_true", default=False)

  arguments = parser.parse_args()

  global DEBUG
  global INFO
  DEBUG = arguments.debug
  INFO = arguments.info

  if INFO: print("Starting the Confluence Label Lifecycle Manager ^_^ !!")
  if DEBUG: print(f"Labels set to: {arguments.freshlabel}, {arguments.stalelabel}, {arguments.rottenlabel}")

  global target_labels
  target_labels = [
    arguments.freshlabel,
    arguments.stalelabel,
    arguments.rottenlabel,
  ]

  configure_atlassian_client(arguments)
  manage_pages_in_space(arguments)

# Check if we're being called as an executable - for example "python main.py" on the CLI
# Otherwise we've been imported into an existing code base
if __name__ == "__main__":
  main()
