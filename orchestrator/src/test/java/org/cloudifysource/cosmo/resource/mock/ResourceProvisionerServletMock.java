/*******************************************************************************
 * Copyright (c) 2013 GigaSpaces Technologies Ltd. All rights reserved
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *       http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *******************************************************************************/
package org.cloudifysource.cosmo.resource.mock;

import javax.ws.rs.GET;
import javax.ws.rs.PUT;
import javax.ws.rs.Path;
import javax.ws.rs.core.Response;

/**
 * A REST servlet that exposes resource provisioning commands of the ResourceProvisionerMock.
 *
 * @author Itai Frenkel
 * @since 0.1
 */
@Path("/")
public class ResourceProvisionerServletMock {

    @PUT
    @Path("/start_virtual_machine/{id}")
    public Response startVirtualMachine() {
        return Response.noContent().build();
    }

    @GET
    @Path("/start_virtual_machine/{id}")
    public Response startVirtualMachineg() {
        return Response.noContent().build();
    }


}