# Solution Management
This outlines the process of downloading and running solutions, validating the output, and uploading the result. All of these steps should be run in a `subprocess`.

## Steps
### 1. Download Solution
- Download a zip using the URL in the synapse

### 2. Validate Tarball
- Security Checks
- Probably check the size of the tarball

### 3. Extract Solution Code
- Extract the code from the tarball (un-tar)

### 4. Validate Code
- Security checks
- Check the size of the un-tar'd files

### 5. Build Docker Image
- Run commands to build a the docker image.

### 6. Validate Docker Image
- Security checks

### 7. Run Solution
- Run the docker image in a container
- Store the output locally

### 8. Validate Solution Output
- Security checks
- Validate the solution against expected output
- Report whether or not the solution passes or fails

### 9. Upload Solution
- If the solution is valid, upload the result
