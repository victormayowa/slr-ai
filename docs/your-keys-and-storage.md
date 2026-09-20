# Your own AI keys and your own storage

OmniReview runs AI tasks and keeps files for you by default. You can also use your own AI provider keys, your own
storage bucket, or both.

## Which API keys your AI tasks use

Under **Settings → AI Provider Keys** you choose one of:

- **Automatic** (the default): your own key for the provider when you have saved one, otherwise the AI included in your
  plan.
- **Only my own keys**: AI tasks always run on your keys. A task stops with a message if you have no key for the
  project's provider, and nothing is sent to the server's account.
- **Only the plan's included AI**: your saved keys are left unused, and AI work counts against your plan's AI credits.

The choice is yours alone and applies to the AI tasks you start, in every project you work on. Other members of a
project keep their own choice. Work on your own key is never charged by OmniReview; work on OmniReview's keys is paid
from your AI balance at the prices on the pricing page.

Add a key for each provider you want to use in the same panel. Keys are encrypted on the server and never shown again
after saving. "Check key" asks the provider whether the key still works.

Some servers are set up without any keys of their own. There, everyone needs their own key, and the panel says so.

## Keeping files in your own bucket

Uploaded and retrieved full texts, analysis plots, individual participant data, submission packages, and deposit
archives are all stored files. By default they are kept in OmniReview's storage and count towards your plan's storage
limit.

Instead, you can connect an S3-compatible bucket you control:

- Amazon S3
- Backblaze B2
- Cloudflare R2
- Wasabi
- MinIO, or another S3-compatible service

Files kept in your bucket do not count towards your plan's storage limit. The plans include 100 MB (Free), 500 MB
(Researcher), and 1 GB (Team); Institution customers connect their own bucket, so their storage is unlimited and their
data stays in their own cloud.

### Where to set it

- **Your own projects:** Settings → File storage.
- **An organization's projects:** Billing → choose the organization → File storage. Organization owners and admins can
  change it.

A project's files follow the account that owns the project: the organization when the project was created in one, and
its creator otherwise.

### What you need

1. A bucket, and optionally a folder inside it, for OmniReview only.
2. An access key that may get, put, delete, and list objects under that folder. For example, for Amazon S3:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
         "Resource": "arn:aws:s3:::my-review-files/omnireview/*"
       },
       {
         "Effect": "Allow",
         "Action": ["s3:ListBucket"],
         "Resource": "arn:aws:s3:::my-review-files",
         "Condition": { "StringLike": { "s3:prefix": ["omnireview/*"] } }
       }
     ]
   }
   ```

3. The endpoint address your provider lists, except for Amazon S3, which uses the region instead.

Nothing is saved until OmniReview has written a test object to the bucket, read it back, and deleted it again. The
access key and secret are encrypted on the server and never shown again. Endpoints must be https addresses on the
public internet.

The browser never talks to your bucket, so no CORS rules are needed. Downloads go through OmniReview, which checks the
person's access to the project first.

### Choosing where new files go

"Use my bucket for new files" and "Use OmniReview's storage for new files" switch between the two at any time. Files
already stored stay where they are, and are always read from where they were written.

### Moving files you already have

"Move existing files to my bucket" and "Move files back to OmniReview" move them in the background, a batch at a time.
Each file is copied, recorded, and only then removed from the old place, so nothing is lost if the move is interrupted.
The panel shows how many files are in each place. If the bucket refuses a file, the move stops and the reason is shown;
fix it and ask again.

You can only disconnect a bucket once no files are left in it.

### Things to know

- **Backups are yours.** Files in your bucket are not part of OmniReview's backups. Turn on versioning and
  server-side encryption in your bucket.
- **Deleting is real.** Deleting a project or a document deletes the file from your bucket too.
- **Keep the bucket private.** Never make it public: full texts are usually licensed for your team only.
- **Costs are yours.** Storage, requests, and download traffic are billed by your storage provider.
