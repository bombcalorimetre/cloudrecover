CloudRecover — Recovery History, Reports & Optional AWS S3 Integration

WHAT THIS PATCH ADDS
- Recovery History section with saved drill totals, pass/fail counts, success rate, and up to 1,000 recent reports.
- GET /api/reports/drills for paginated-by-limit history/summary (limit 1–1000).
- GET /api/reports/drills.csv to export every persisted drill record, including step summaries, as CSV.
- Browser Print / Save PDF button for a printable dashboard report. Choose “Save to PDF” in the browser's print dialog.
- Optional AWS connector using boto3 and the standard AWS credential chain.
- AWS setup check, read-only S3 bucket listing, and read-only object metadata inspection (up to 1,000 objects per request).
- Audit events when AWS bucket/object inventory is requested.
- No AWS credentials are stored in the application database or frontend.

SAFETY SCOPE
- AWS features are READ-ONLY: only ListBuckets and ListObjectsV2 are called. CloudRecover does not upload, delete, overwrite, restore, or change AWS resources.
- S3 integration only inspects object metadata (key, size, last modified, ETag); it does not download object contents or validate a real backup's integrity.
- AWS integration is optional. The app's local simulation works without boto3 or AWS credentials.
- Never paste AWS access keys into the frontend, source code, or chat. Prefer an IAM role or a named AWS CLI profile.

INSTALL / UPDATE
1. Stop the backend and frontend development servers.
2. From the CloudRecover project root, back up the files that will be replaced:
   cp backend/main.py backend/main.py.before-reports-aws
   cp frontend/src/App.jsx frontend/src/App.jsx.before-reports-aws
   cp frontend/src/App.css frontend/src/App.css.before-reports-aws
3. Copy this ZIP to the project root (the folder containing backend/ and frontend/).
4. Extract the patch from the project root:
   unzip -o CloudRecover-reports-aws-patch.zip
5. Start the backend from the project root:
   source .venv/bin/activate
   uvicorn main:app --app-dir backend --reload
6. In another terminal:
   cd frontend
   npm run dev
7. Open http://localhost:5173. Use Reports for drill history and exports. Use AWS to configure/check the optional connector.

CSV AND PDF REPORTS
- Reports > Export CSV downloads all saved recovery drills from SQLite, not only the latest 20 dashboard entries.
- Reports > Print / Save PDF opens the browser's print dialog. Select “Save to PDF”. The print stylesheet hides navigation and cloud connector controls and formats report sections for printing.
- The history panel shows up to 1,000 newest reports. The CSV endpoint exports all reports.

OPTIONAL AWS SETUP
1. Install the optional SDK in the same Python virtual environment used to run FastAPI:
   pip install -r requirements-aws.txt
2. Configure credentials using a standard supported method, for example a named AWS CLI profile:
   aws configure --profile cloudrecover-readonly
   export AWS_PROFILE=cloudrecover-readonly
   export AWS_DEFAULT_REGION=ap-south-1
   The profile should be authorized only for the S3 listing actions you need. An IAM role is preferable when running on AWS.
3. In the dashboard's AWS section, click “Check setup”, then “List S3 buckets”. Select a bucket and optionally enter a prefix, then click “Inspect objects”.
4. The backend uses the default boto3 credential chain. It does not display or save access keys.

MINIMUM IAM PERMISSIONS (READ-ONLY EXAMPLE)
Replace example-bucket with the intended bucket. Listing all bucket names requires the account-level ListAllMyBuckets action; if you don't want to grant that action, omit bucket discovery and use a separately scoped implementation for known bucket names.

{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListBucketNames",
      "Effect": "Allow",
      "Action": "s3:ListAllMyBuckets",
      "Resource": "*"
    },
    {
      "Sid": "ListObjectMetadataInOneBucket",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::example-bucket"
    }
  ]
}

This is a template, not a complete organization policy. Have your AWS administrator review it before use. Bucket policies, organization SCPs, region/configuration, or permission boundaries may further restrict access.

VALIDATION PERFORMED
- Backend Python syntax compilation passed.
- FastAPI smoke tests passed for recovery history, CSV export, AWS status, and a sample recovery drill/history round trip.
- Frontend JSX parsed successfully with TypeScript's JSX parser.
- AWS live inventory was not tested against your AWS account; it requires your own SDK configuration and IAM permissions.

ROLLBACK
Stop the app and restore the three .before-reports-aws files. requirements-aws.txt is optional and can remain in the project. New report endpoints are ignored by older frontend code.
