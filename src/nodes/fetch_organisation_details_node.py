"""
Fetch Organisation Details Node
Retrieves organisation configuration details from Supabase and stores them in state.
"""

from src.services.supabase_service import supabase_service


def fetch_organisation_details(state: dict, phone_number_id: str = None):
    """
    Fetch organisation details from Supabase based on agent phone number.
    Only fetches from Supabase on the first message of a conversation;
    subsequent messages reuse the details already stored in top-level state.
    
    Args:
        state: Current state dictionary
        agent_phonenumber: The agent's phone number to fetch org details for.
                          If not provided, extracts from state["graph_state"]["agent_phonenumber"]
    
    Returns:
        Updated state with organisation_details
    """
    try:
        existing_details = state.get("organisation_details")
        existing_locations = state.get("organisation_locations")
        
        if existing_details:
            print(f"✅ Organisation details already in state, skipping Supabase fetch")
            if "graph_state" not in state:
                state["graph_state"] = {}
            state["graph_state"]["organisation_details"] = existing_details
            
            # Also restore locations if they exist in state
            if existing_locations:
                state["graph_state"]["organisation_locations"] = existing_locations
                print(f"✅ Organisation locations restored from state")
            
            return state

        # If phone_number_id not provided, try to get from state
        if not phone_number_id:
            graph_state = state.get("graph_state", {})
            agent_phonenumber = graph_state.get("agent_phonenumber")
            phone_number_id = graph_state.get("phone_number_id")
        
        if not phone_number_id:
            print("❌ No phone number id provided to fetch organisation details")
            state["organisation_details"] = None
            if "graph_state" not in state:
                state["graph_state"] = {}
            state["graph_state"]["organisation_details"] = None
            return state

        print(f"🔍 Fetching organisation details for agent: {phone_number_id}")

        # Query organisation details from Supabase
        print(f"🔍 phone_number_id type: {type(phone_number_id)}, value: {phone_number_id}")
        whatsapp_data = supabase_service.query(
            table="organisation_whatsapp_integration",
            filters={"phone_number_id": str(phone_number_id)}
        )
        print(f"📊 Supabase query result for organisation_whatsapp_integration: {whatsapp_data}")
        if not whatsapp_data:
            raise ValueError(f"No organisation found for phone_number_id: {phone_number_id}")

        organisation_id = whatsapp_data[0].get("organisation_id")


        org_details = supabase_service.query(
            table="organisation_details",
            filters={"organisation_id": organisation_id}
        )
        print(f"📊 Supabase query result for organisation_details: {org_details}")
        org_locations = supabase_service.query(
            table="organisation_locations",
            filters={"organisation_id": org_details[0].get("organisation_id") if org_details else None}
        )
        print(f"📊 Supabase query result for organisation_locations: {org_locations}")
        
        if org_details:
            org_id = org_details[0].get("organisation_id")
            # Fetch related locations from organisation_locations
            locations_res = supabase_service.query(
                table="organisation_locations",
                filters={"organisation_id": org_id}
            )
            org_details[0]["locations"] = locations_res or []
            
            # Store in top-level state (persisted by LangGraph checkpointer) and graph_state
            state["organisation_details"] = org_details[0]
            if "graph_state" not in state:
                state["graph_state"] = {}
            state["graph_state"]["organisation_details"] = org_details[0]
            print(f"✅ Organisation details & {len(org_details[0]['locations'])} locations fetched: {org_details[0].get('organisation_name', 'Unknown')}")
        else:
            print(f"⚠️ No organisation details found for agent: {agent_phonenumber}")
            state["organisation_details"] = {}
            if "graph_state" not in state:
                state["graph_state"] = {}
            state["graph_state"]["organisation_details"] = None


        if org_locations:
            # Store in top-level state (persisted by LangGraph checkpointer) and graph_state
            state["organisation_locations"] = org_locations
            if "graph_state" not in state:
                state["graph_state"] = {}
            state["graph_state"]["organisation_locations"] = org_locations
            print(f"✅ Organisation locations fetched: {org_locations}")
        else:
            print(f"⚠️ No organisation location found for agent: {agent_phonenumber}")
            state["organisation_locations"] = {}
            if "graph_state" not in state:
                state["graph_state"] = {}
            state["graph_state"]["organisation_locations"] = None
        
        return state
        
    except Exception as e:
        print(f"❌ Error fetching organisation details: {e}")
        state["organisation_details"] = None
        if "graph_state" not in state:
            state["graph_state"] = {}
        state["graph_state"]["organisation_details"] = None
        return state


def fetch_organisation_details_by_id(state: dict, organisation_id: str):
    """
    Fetch organisation details by organisation_id.
    
    Args:
        state: Current state dictionary
        organisation_id: UUID of the organisation
    
    Returns:
        Updated state with organisation_details
    """
    try:
        org_details = supabase_service.get_by_id(
            table="organisation_details",
            id=organisation_id
        )
        
        if org_details:
            # Fetch related locations
            locations_res = supabase_service.query(
                table="organisation_locations",
                filters={"organisation_id": organisation_id}
            )
            org_details["locations"] = locations_res or []
            
            state["organisation_details"] = org_details
            if "graph_state" not in state:
                state["graph_state"] = {}
            state["graph_state"]["organisation_details"] = org_details
            print(f"✅ Organisation details & {len(org_details['locations'])} locations fetched: {org_details.get('organisation_name', 'Unknown')}")
        else:
            print(f"⚠️ No organisation found with ID: {organisation_id}")
            state["organisation_details"] = None
            if "graph_state" not in state:
                state["graph_state"] = {}
            state["graph_state"]["organisation_details"] = None
        
        return state
        
    except Exception as e:
        print(f"❌ Error fetching organisation details by ID: {e}")
        state["organisation_details"] = None
        if "graph_state" not in state:
            state["graph_state"] = {}
        state["graph_state"]["organisation_details"] = None
        return state
