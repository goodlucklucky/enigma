# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from enum import Enum


class ValidationErrors(Enum):
    INVALID_TARBALL = "The tarball is invalid. It may be corrupted or not a tarball at all."
    TARBALL_DOWNLOAD_FAILED = "Failed to download the tarball from the provided URL."
    MISSING_DOCKERFILE = "The tarball is missing a Dockerfile in the root directory."
    DOCKER_BUILD_FAILED = "Docker failed to build the image from the provided Dockerfile."
    DOCKER_RUN_FAILED = "Docker failed to run the container from the built image."
    DOCKER_IMAGE_VALIDATION_FAILED = "The built Docker image failed validation checks and cannot be run."
    INVALID_PROGRAM = "The program provided in the tarball is invalid. It may be missing required files, have syntax errors, or fail other validation checks."
