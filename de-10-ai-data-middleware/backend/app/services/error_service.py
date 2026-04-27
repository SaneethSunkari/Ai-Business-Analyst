import re


def clean_db_error_message(error: str) -> str:
    cleaned_error = error.strip()
    cleaned_error = re.sub(r"\n+\(Background on this error at:.*$", "", cleaned_error, flags=re.DOTALL)
    cleaned_error = re.sub(r"^\([^)]+\)\s*", "", cleaned_error)
    cleaned_error = cleaned_error.strip()
    normalized_error = cleaned_error.lower()

    if "only select queries are allowed" in normalized_error:
        return "Only SELECT queries are allowed"
    if "multiple sql statements are not allowed" in normalized_error:
        return "Multiple SQL statements are not allowed"
    if "password authentication failed" in normalized_error:
        return "Unable to connect to the database with the provided credentials"
    if "access denied for user" in normalized_error:
        return "Unable to connect to the database with the provided credentials"
    if "login failed for user" in normalized_error:
        return "Unable to connect to the database with the provided credentials"
    if "ora-01017" in normalized_error:
        return "Unable to connect to the database with the provided credentials"
    if "ora-12514" in normalized_error:
        return "Oracle service name is invalid or not reachable"
    if "ora-12154" in normalized_error:
        return "Oracle connection identifier could not be resolved"
    if "incorrect username or password was specified" in normalized_error:
        return "Unable to connect to Snowflake with the provided credentials"
    if "warehouse" in normalized_error and "does not exist or not authorized" in normalized_error:
        return "Snowflake warehouse not found or not accessible"
    if "schema" in normalized_error and "does not exist or not authorized" in normalized_error:
        return "Snowflake schema not found or not accessible"
    if "database" in normalized_error and "does not exist or not authorized" in normalized_error:
        return "Snowflake database not found or not accessible"
    if "account is empty" in normalized_error or "account must be specified" in normalized_error:
        return "Snowflake account identifier is required"
    if "headbucket" in normalized_error and "403" in normalized_error:
        return "Unable to access the S3 bucket with the provided credentials"
    if "headbucket" in normalized_error and "404" in normalized_error:
        return "S3 bucket not found"
    if "nosuchbucket" in normalized_error:
        return "S3 bucket not found"
    if "invalidaccesskeyid" in normalized_error or "signaturedoesnotmatch" in normalized_error:
        return "Unable to access Amazon S3 with the provided credentials"
    if "container" in normalized_error and "not found" in normalized_error:
        return "Azure Blob container not found"
    if "authenticationfailed" in normalized_error or "authorizationpermissionmismatch" in normalized_error:
        return "Unable to access Azure Blob Storage with the provided credentials"
    if "azure blob requires either a connection string" in normalized_error:
        return "Azure Blob requires a connection string, account URL plus SAS, or account name plus account key"
    if "no matching files were found" in normalized_error:
        return "No matching files were found for the selected source path"
    if "could not infer file format" in normalized_error:
        return "Could not infer the source file format. Set options.file_format to parquet, csv, or json"
    if "matched object set is too large" in normalized_error or "matched blob set is too large" in normalized_error:
        return "Selected source path is too large for the current scan limit. Narrow the prefix or reduce the file count"
    if "default credentials were not found" in normalized_error:
        return "BigQuery credentials were not found. Provide a credentials JSON path or configure Google application default credentials."
    if "permission denied while getting drive credentials" in normalized_error:
        return "BigQuery credentials are missing or not authorized"
    if "404 not found" in normalized_error and "dataset" in normalized_error:
        return "BigQuery dataset not found or not accessible"
    if "invalid access token" in normalized_error and "databricks" in normalized_error:
        return "Unable to connect to Databricks SQL with the provided access token"
    if "http path" in normalized_error and "databricks" in normalized_error:
        return "Databricks HTTP Path is invalid or not reachable"
    if "unable to locate credentials" in normalized_error:
        return "Cloud credentials were not found or are not configured correctly"
    if "unrecognizedclientexception" in normalized_error or "invalidclienttokenid" in normalized_error:
        return "AWS credentials are invalid or not authorized"
    if "salesforceauthenticationfailed" in normalized_error or "invalid_login" in normalized_error:
        return "Unable to connect to Salesforce with the provided credentials"
    if "serverselectiontimeouterror" in normalized_error:
        return "Unable to connect to the MongoDB server"
    if "authentication failed" in normalized_error and "mongo" in normalized_error:
        return "Unable to connect to MongoDB with the provided credentials"
    if "nobrokersavailable" in normalized_error:
        return "Unable to connect to the Kafka brokers"
    if "kafka topic" in normalized_error and "not found" in normalized_error:
        return first_line if (first_line := cleaned_error.splitlines()[0].strip()) else "Kafka topic not found"
    if "status code 401" in normalized_error and "dremio" in normalized_error:
        return "Unable to connect to Dremio with the provided access token"
    if "status code 404" in normalized_error and "dremio" in normalized_error:
        return "Dremio project or endpoint was not found"
    if "role \"" in normalized_error and "does not exist" in normalized_error:
        return "Unable to connect to the database with the provided credentials"
    if "connection refused" in normalized_error or "could not connect to server" in normalized_error:
        return "Unable to connect to the database server"
    if "can't connect to mysql server" in normalized_error:
        return "Unable to connect to the database server"
    if "relation \"" in normalized_error and "does not exist" in normalized_error:
        return "Generated SQL referenced a table that does not exist"
    if "invalid object name" in normalized_error:
        return "Generated SQL referenced a table that does not exist"
    if "table or view does not exist" in normalized_error:
        return "Generated SQL referenced a table that does not exist"
    if "column \"" in normalized_error and "does not exist" in normalized_error:
        return "Generated SQL referenced a column that does not exist"
    if "invalid identifier" in normalized_error:
        return "Generated SQL referenced a column that does not exist"
    if "unrecognized name:" in normalized_error:
        return "Generated SQL referenced a column that does not exist"
    if "syntax error at or near" in normalized_error:
        return "Generated SQL was invalid"
    if "sql compilation error" in normalized_error:
        return "Generated SQL was invalid"
    if not cleaned_error:
        return "Query failed"
    first_line = cleaned_error.splitlines()[0].strip()
    return first_line or "Query failed"
