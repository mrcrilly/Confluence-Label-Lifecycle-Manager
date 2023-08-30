# Confluence Label Lifecycle Manager

Manages the lifecycle phase labels of pages in a given Confluence space.

## Reporting Page

This scripts edits a page in your Confluence space. The ID for that page must be set using the `--pageid` flag.

! Work in progress.

## Environment Setup

The `requirements.txt` file contains all the Python libraries needed to operate this script.

They should be configured and used via a virtual environment:

```shell
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

The virtual environment should then be used to invoke the `main.py` script (documented below.)

## Invocation

Once you've configured the environment, executing the script requires passing a few mandatory flags:

```
python main.py -u USERNAME -p PASSWORD --updatepage
```

Where `USERNAME` is an non-human account username and the `PASSWORD` is an API access token.

The `--updatepage` flag is required *if* you want to update the reporting page in Confluence (see above.) Without this flag you will **not** update that page and your run (and your time) could be wasted.

### Debugging

If you're executing the script locally to debug or extend the script, then the `-d` flag will generate *a lot* of debugging information.

## Getting Help

The script uses `argparse`, which provide helpful information when the script is called with `-h`:

```
usage: main.py [-h] -u USERNAME -p PASSWORD [-H HOSTNAME] [-s SPACE] [-m MAXPAGES] [-U] [-I PAGEID] [-T PAGETITLE] [-c] [-d] [-S STALE] [-R ROTTEN]

Confluence Page Lifecycle Processor

options:
  -h, --help            show this help message and exit
  -u USERNAME, --username USERNAME
                        The Atlassian user to authenticate as.
  -p PASSWORD, --password PASSWORD
                        The Atlassian password to authenticate with.
  -H HOSTNAME, --hostname HOSTNAME
                        The Atlassian URL/hostname to authenticate to.
  -s SPACE, --space SPACE
                        The Space inside of the Confluence account.
  -m MAXPAGES, --maxpages MAXPAGES
                        The number of pages to process, maximum.
  -U, --updatepage      Update the Lifecycle Report page in Confluence.
  -I PAGEID, --pageid PAGEID
                        The Lifecycle Report page ID.
  -T PAGETITLE, --pagetitle PAGETITLE
                        The Lifecycle Report page title.
  -c, --cloud           Whether or not the Atlassian instance is Cloud based.
  -d, --debug           Enable debugging output
  -S STALE, --stale STALE
                        Number of days passed until a page is considered stale
  -R ROTTEN, --rotten ROTTEN
                        Number of days passed until a page is considered rotten
```
