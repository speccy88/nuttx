/****************************************************************************
 * tools/p2/libp2-shims/stdio.h
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.  The
 * ASF licenses this file to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance with the
 * License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
 * WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
 * License for the specific language governing permissions and limitations
 * under the License.
 *
 ****************************************************************************/

/* The pinned p2llvm libp2 memcpy implementation includes <stdio.h>, but it
 * uses no stdio declarations.  propeller2.h, included immediately before it,
 * already defines the size_t and NULL names that memcpy needs.  This empty
 * build-only shim lets libp2 be compiled without installing or linking the
 * p2llvm libc.  It is exposed only while building libp2, never while
 * building LLVM or NuttX.
 */

#ifndef __TOOLS_P2_LIBP2_SHIMS_STDIO_H
#define __TOOLS_P2_LIBP2_SHIMS_STDIO_H

#endif /* __TOOLS_P2_LIBP2_SHIMS_STDIO_H */
