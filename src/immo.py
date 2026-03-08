import json
import os
import os.path
import time
import traceback
from datetime import datetime
from json import JSONDecodeError
from subprocess import call

from dotenv import load_dotenv

import submit

load_dotenv()

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
fname = "href.json"

while True:
    try:
        if os.path.isfile(fname):
            call(["mv", fname, "href_old.json"])
            call(["scrapy", "crawl", "immoscout", "-o", "href.json", "-s", "LOG_ENABLED=false"])
            with open('href.json') as data_file:
                data = json.load(data_file)
            data = list(set([i['href'] for i in data]))
            with open('href_old.json') as data_old_file:
                data_old = json.load(data_old_file)
            data_old = list(set([i['href'] for i in data_old]))
        else:
            call(["scrapy", "crawl", "immoscout", "-o", "href.json", "-s", "LOG_ENABLED=false"])
            with open('href.json') as data_file:
                data = json.load(data_file)
            data = list(set([i['href'] for i in data]))
            print(data)
            ini = input("No href.json file found. Sending messages to all offers found above? (y/n)\n")
            if ini.lower() == "y":
                data_old = []
            elif ini.lower() == "n":
                call(["cp", fname, "href_old.json"])
                with open('href_old.json') as data_old_file:
                    data_old = json.load(data_old_file)
                data_old = list(set([i['href'] for i in data_old]))

        # Black list
        if os.path.isfile('blacklist.json'):
            with open('blacklist.json') as blacklist_file:
                blacklist = json.load(blacklist_file)
            blacklist = list(set([i['href'] for i in blacklist]))
        else:
            blacklist = []
        print("Blacklist: ", blacklist)

        diff_id = list(set(data) - set(data_old) - set(blacklist))
        text_file = open("sent_request.dat", "a")
        text_file1 = open("diff.dat", "a")
        if len(diff_id) != 0:
            print(len(diff_id), "new offers found")
            print("New offers id: ", diff_id)
            for new in diff_id:
                print("Sending message to: ", new)
                submit.submit_app(new)
                text_file.write("ID: %s \n" % new)
                text_file.write(str(datetime.now()) + '\n')
                text_file1.write(str(new) + '\n')
            text_file.close()
            text_file1.close()
        else:
            print("No new offers.")

    except JSONDecodeError as e:
        print("There was a problem with reading a json formatted object")
        print("".join(traceback.TracebackException.from_exception(e).format()))
    except Exception as e:
        print(f"Unexpected error: {e}")
        print("".join(traceback.TracebackException.from_exception(e).format()))
    finally:
        print("Time: ", datetime.now())
        print("Sleeping for", CHECK_INTERVAL, "seconds...")
        time.sleep(CHECK_INTERVAL)
