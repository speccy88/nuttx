/****************************************************************************
 * arch/p2/src/common/p2_overlay_internal.h
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

#ifndef __ARCH_P2_SRC_COMMON_P2_OVERLAY_INTERNAL_H
#define __ARCH_P2_SRC_COMMON_P2_OVERLAY_INTERNAL_H

/* p2_overlay_dispatch_enter() returns a Hub target in the low 20 bits.  Bit
 * 31 marks a same-group call whose original CALLA resume must remain on the
 * task stack.  The assembly veneer clears this private marker before jumping
 * to the target.  It is never part of the public container or metadata ABI.
 */

#define P2_OVERLAY_DIRECT_TARGET_BIT   31
#define P2_OVERLAY_DIRECT_TARGET_FLAG  0x80000000

#endif /* __ARCH_P2_SRC_COMMON_P2_OVERLAY_INTERNAL_H */
